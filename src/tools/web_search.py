"""
Web search tool — queries SearXNG and returns extracted page content.
"""

import logging
from dataclasses import dataclass

import httpx
import trafilatura

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
) -> list[SearchResult]:
    """
    Search SearXNG and return up to max_results with extracted page content.

    Args:
        query:                 Search query string.
        searxng_url:           Base URL of the SearXNG instance.
        max_results:           Maximum number of results to return.
        max_tokens_per_result: Token budget per result for content extraction.

    Returns:
        List of SearchResult objects.
    """
    search_url = f"{searxng_url.rstrip('/')}/search"
    params = {"q": query, "format": "json", "language": "en"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(search_url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"SearXNG request failed: {e}")
            return []

        data = resp.json()
        raw_results = data.get("results", [])[:max_results]

        results: list[SearchResult] = []
        for item in raw_results:
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("content", "")

            content = await _fetch_and_extract(url, max_tokens_per_result, client)
            if not content:
                # Fall back to snippet if extraction failed
                content = _truncate_to_tokens(snippet, max_tokens_per_result)

            results.append(SearchResult(url=url, title=title, snippet=snippet, content=content))

        return results


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
