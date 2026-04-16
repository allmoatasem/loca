from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class EpubAdapter(BaseAdapter):
    source_name = "epub"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".epub"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            from html.parser import HTMLParser  # noqa: PLC0415

            import ebooklib  # noqa: PLC0415
            from ebooklib import epub  # noqa: PLC0415
        except ImportError:
            return []

        class _TextExtractor(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str) -> None:
                self.parts.append(data)

            def get_text(self) -> str:
                return " ".join(self.parts).strip()

        book = epub.read_epub(str(path))
        chunks: list[Chunk] = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            parser = _TextExtractor()
            parser.feed(item.get_content().decode("utf-8", errors="replace"))
            text = parser.get_text()
            if text:
                chunks.append(Chunk(
                    text=text,
                    source="epub",
                    title=f"{path.name} — {item.get_name()}",
                    created_at="",
                    metadata={"path": str(path), "item": item.get_name()},
                ))
        return chunks
