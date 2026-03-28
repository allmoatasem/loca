"""
Web search tool — queries SearXNG and returns extracted page content.

Research mode (research_mode=True):
  Uses Playwright instead of trafilatura to fetch each result URL, giving
  richer content from JS-rendered pages.

SearXNG fallback (when SearXNG returns 0 results):
  Automatically falls back to playwright_search() which uses DuckDuckGo
  via a headless browser.
"""

import logging
from dataclasses import dataclass

import httpx
import trafilatura

from .playwright_fetch import playwright_fetch, playwright_search

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    content: str  # extracted full text (up to token budget)


def _count_tokens(text: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " [truncated]"


async def _fetch_and_extract(url: str, max_tokens: int, client: httpx.AsyncClient) -> str:
    """Download a page and extract readable text with trafilatura."""
    try:
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, include_links=False, include_tables=True)
        if not text:
            return ""
        return _truncate_to_tokens(text, max_tokens)
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return ""


async def web_search(
    query: str,
    searxng_url: str,
    max_results: int = 5,
    max_tokens_per_result: int = 500,
    research_mode: bool = False,
) -> list[SearchResult]:
    """
    Search SearXNG and return up to max_results with extracted page content.

    Args:
        query:                 Search query string.
        searxng_url:           Base URL of the SearXNG instance.
        max_results:           Maximum number of results to return.
        max_tokens_per_result: Token budget per result for content extraction.
        research_mode:         If True, use Playwright for richer JS-rendered content.

    Returns:
        List of SearchResult objects (falls back to Playwright search if SearXNG empty).
    """
    search_url = f"{searxng_url.rstrip('/')}/search"
    params = {"q": query, "format": "json", "language": "en"}
    max_chars = max_tokens_per_result * 4

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(search_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"SearXNG request failed: {e}")
            data = {}

        raw_results = data.get("results", [])[:max_results]

    # ── SearXNG fallback: browser search when no results ─────────────────────
    if not raw_results:
        logger.info("SearXNG returned 0 results — falling back to Playwright search")
        pw_items = await playwright_search(
            query,
            max_results=max_results,
            max_chars_per=max_chars,
        )
        return [
            SearchResult(
                url=it.get("url", ""),
                title=it.get("title", ""),
                snippet=it.get("snippet", ""),
                content=it.get("content", it.get("snippet", "")),
            )
            for it in pw_items
        ]

    # ── Normal path: SearXNG returned results ────────────────────────────────
    results: list[SearchResult] = []

    if research_mode:
        # Research mode: run SearXNG + Playwright browser search in parallel,
        # then fetch all unique URLs with Playwright for richer JS-rendered content.
        import asyncio as _asyncio
        pw_task = _asyncio.create_task(
            playwright_search(query, max_results=max_results, max_chars_per=max_chars)
        )

        # Fetch SearXNG URLs via Playwright
        seen_urls: set[str] = set()
        async with httpx.AsyncClient(timeout=15.0) as client:
            for item in raw_results:
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                pw = await playwright_fetch(url, max_chars=max_chars)
                content = pw["content"] or _truncate_to_tokens(item.get("content", ""), max_tokens_per_result)
                results.append(SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    content=content,
                ))

        # Merge Playwright browser results (deduped)
        pw_items = await pw_task
        for it in pw_items:
            url = it.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(SearchResult(
                    url=url,
                    title=it.get("title", ""),
                    snippet=it.get("snippet", ""),
                    content=it.get("content", it.get("snippet", "")),
                ))
    else:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for item in raw_results:
                url     = item.get("url", "")
                title   = item.get("title", "")
                snippet = item.get("content", "")
                content = await _fetch_and_extract(url, max_tokens_per_result, client)
                if not content:
                    content = _truncate_to_tokens(snippet, max_tokens_per_result)
                results.append(SearchResult(url=url, title=title, snippet=snippet, content=content))

    return results[:max_results]


def format_search_results(results: list[SearchResult]) -> str:
    """Format search results into the XML injection format the spec defines."""
    if not results:
        return "<search_results>\n  No results found.\n</search_results>"

    parts = ["<search_results>"]
    for r in results:
        parts.append(
            f'  <result url="{r.url}" title="{r.title}" snippet="{r.snippet}">\n'
            f"    {r.content}\n"
            f"  </result>"
        )
    parts.append("</search_results>")
    parts.append(
        "\nAnswer the user's question using the search results above as context. "
        "Cite sources by URL when making specific claims."
    )
    return "\n".join(parts)
