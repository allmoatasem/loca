from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class WebAdapter(BaseAdapter):
    source_name = "web"

    def can_handle(self, path: Path) -> bool:
        s = str(path)
        return s.startswith("http:/") or s.startswith("https:/")

    def extract(self, path: Path) -> list[Chunk]:
        url = str(path)
        try:
            import trafilatura  # noqa: PLC0415
            downloaded = trafilatura.fetch_url(url)
            text = trafilatura.extract(downloaded) or ""
        except Exception:
            return []
        if not text.strip():
            return []
        return [Chunk(
            text=text.strip(),
            source="web",
            title=url,
            created_at="",
            metadata={"url": url},
        )]
