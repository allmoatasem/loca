from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class PDFAdapter(BaseAdapter):
    source_name = "pdf"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".pdf"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            from pypdf import PdfReader  # noqa: PLC0415
        except ImportError:
            return []
        reader = PdfReader(str(path))
        chunks: list[Chunk] = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(Chunk(
                    text=text,
                    source="pdf",
                    title=f"{path.name} — page {i + 1}",
                    created_at="",
                    metadata={"path": str(path), "page": i + 1},
                ))
        return chunks
