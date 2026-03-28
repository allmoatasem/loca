"""
Playwright-based page fetcher and search fallback.

Used in two scenarios:
  1. Research mode — fetch full JS-rendered content for each SearXNG result URL.
  2. SearXNG fallback — when SearXNG returns no results, search via DuckDuckGo
     using a headless browser.

Playwright + Chromium must be installed:
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_JS_CLEAN = """() => {
    ['script','style','nav','footer','header','aside',
     '[class*="cookie"]','[class*="banner"]','[id*="ad"]'].forEach(s => {
        try { document.querySelectorAll(s).forEach(el => el.remove()); } catch(_){}
    });
    return document.body?.innerText || '';
}"""


async def playwright_fetch(url: str, max_chars: int = 10_000) -> dict:
    """
    Fetch a URL with a headless browser (handles JS-rendered content).
    Returns {"url": str, "content": str, "error": str | None}
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"url": url, "content": "", "error": "playwright not installed — run: pip install playwright && playwright install chromium"}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({"User-Agent": _UA})
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(1_200)
            content: str = await page.evaluate(_JS_CLEAN)
            await browser.close()

        content = " ".join(content.split())
        if len(content) > max_chars:
            content = content[:max_chars] + " [truncated]"
        return {"url": url, "content": content, "error": None}

    except Exception as exc:
        logger.warning("playwright_fetch failed for %s: %s", url, exc)
        return {"url": url, "content": "", "error": str(exc)}


async def playwright_search(
    query: str,
    max_results: int = 5,
    max_chars_per: int = 4_000,
) -> list[dict]:
    """
    Search DuckDuckGo (HTML endpoint) with a headless browser and fetch top pages.
    Used as a fallback when SearXNG returns nothing, or in research mode.
    Returns a list of {"url", "title", "snippet", "content"} dicts.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed — cannot use browser search fallback")
        return []

    results: list[dict] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            # Step 1: SERP via DuckDuckGo HTML (no JS required → faster, more reliable)
            page = await browser.new_page()
            await page.set_extra_http_headers({"User-Agent": _UA})
            ddg = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
            await page.goto(ddg, wait_until="networkidle", timeout=25_000)

            items: list[dict] = await page.evaluate("""() => {
                const out = [];
                // DuckDuckGo HTML results — try several known selectors
                const links = document.querySelectorAll(
                    '.result__a, .result__url, [data-testid="result-title-a"], h2 a'
                );
                links.forEach(a => {
                    if (out.length >= 12) return;
                    const href = a.getAttribute('href') || '';
                    // DDG HTML wraps redirect URLs — extract real URL
                    let url = href;
                    try {
                        const u = new URL(href, location.href);
                        url = u.searchParams.get('uddg') || u.searchParams.get('u') || href;
                    } catch(_) {}
                    if (!url || !url.startsWith('http')) return;
                    const result = a.closest('.result, .web-result, [data-result]');
                    const snippet = result?.querySelector(
                        '.result__snippet, .result__body, [data-result="snippet"]'
                    )?.innerText || '';
                    out.push({ url, title: a.innerText.trim(), snippet });
                });
                return out;
            }""")
            await page.close()

            logger.info("playwright_search: found %d SERP items for %r", len(items), query)

            # Step 2: fetch each result page for full content
            for item in items[:max_results]:
                url = item.get("url", "")
                if not url or not url.startswith("http"):
                    continue
                try:
                    p2 = await browser.new_page()
                    await p2.set_extra_http_headers({"User-Agent": _UA})
                    await p2.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    await p2.wait_for_timeout(1_000)
                    content: str = await p2.evaluate(_JS_CLEAN)
                    await p2.close()
                    content = " ".join(content.split())
                    if len(content) > max_chars_per:
                        content = content[:max_chars_per] + " [truncated]"
                    item["content"] = content
                except Exception as e:
                    logger.debug("playwright page fetch failed for %s: %s", url, e)
                    item["content"] = item.get("snippet", "")
                results.append(item)

            await browser.close()

    except Exception as exc:
        logger.warning("playwright_search failed: %s", exc)

    return results
