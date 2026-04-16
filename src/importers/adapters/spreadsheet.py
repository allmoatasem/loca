from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk

_SUPPORTED = {".csv", ".xlsx"}


class SpreadsheetAdapter(BaseAdapter):
    source_name = "spreadsheet"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _SUPPORTED

    def extract(self, path: Path) -> list[Chunk]:
        try:
            import pandas as pd  # noqa: PLC0415
        except ImportError:
            return []
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, dtype=str).fillna("")
        else:
            df = pd.read_excel(path, dtype=str).fillna("")
        chunks: list[Chunk] = []
        for _, row in df.iterrows():
            text = ", ".join(f"{col}: {val}" for col, val in row.items() if val)
            if text:
                chunks.append(Chunk(
                    text=text,
                    source="spreadsheet",
                    title=path.name,
                    created_at="",
                    metadata={"path": str(path)},
                ))
        return chunks
