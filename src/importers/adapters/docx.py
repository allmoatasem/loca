from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class DocxAdapter(BaseAdapter):
    source_name = "docx"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".docx"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            from docx import Document  # noqa: PLC0415
        except ImportError:
            return []
        doc = Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return []
        return [Chunk(
            text="\n".join(paragraphs),
            source="docx",
            title=path.name,
            created_at="",
            metadata={"path": str(path)},
        )]
