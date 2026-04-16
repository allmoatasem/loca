from __future__ import annotations

import re
from pathlib import Path

from ..base import BaseAdapter, Chunk

_SUPPORTED = {".md", ".txt", ".rst"}


class MarkdownAdapter(BaseAdapter):
    source_name = "markdown"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _SUPPORTED

    def extract(self, path: Path) -> list[Chunk]:
        text = path.read_text(encoding="utf-8", errors="replace")
        sections = self._split(text)
        return [
            Chunk(
                text=s.strip(),
                source="markdown",
                title=path.name,
                created_at="",
                metadata={"path": str(path)},
            )
            for s in sections if s.strip()
        ]

    def _split(self, text: str) -> list[str]:
        """Split on # / ## / ### headings. Fall back to whole text if none found."""
        parts = re.split(r"(?m)^#{1,3} ", text)
        parts = [p.strip() for p in parts if p.strip()]
        return parts if len(parts) > 1 else [text]
