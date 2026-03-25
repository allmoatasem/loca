"""
Web fetch tool — download and extract readable content from a specific URL.
"""

import logging

import httpx
import trafilatura

logger = logging.getLogger(__name__)

_MAX_RESPONSE_CHARS = 8000  # ~2000 tokens


async def web_fetch(url: str, max_chars: int = _MAX_RESPONSE_CHARS) -> dict:
    """
    Fetch a URL and return extracted readable text.

    Returns:
        {"url": str, "content": str, "error": str | None}
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"web_fetch failed for {url}: {e}")
            return {"url": url, "content": "", "error": str(e)}

    text = trafilatura.extract(resp.text, include_links=True, include_tables=True)
    if not text:
        # fallback: strip tags manually
        import re
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = " ".join(text.split())

    if len(text) > max_chars:
        text = text[:max_chars] + " [truncated]"

    return {"url": url, "content": text, "error": None}
