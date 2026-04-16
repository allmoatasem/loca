from __future__ import annotations

import json
from pathlib import Path

from ..base import BaseAdapter, Chunk


def _flatten(obj: object, prefix: str = "") -> list[str]:
    """Recursively flatten a JSON object to 'key: value' lines."""
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lines.extend(_flatten(v, f"{prefix}{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            lines.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        lines.append(f"{prefix}: {obj}")
    return lines


class JSONAdapter(BaseAdapter):
    source_name = "json"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".json"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        text = "\n".join(_flatten(data))
        if not text.strip():
            return []
        return [Chunk(
            text=text,
            source="json",
            title=path.name,
            created_at="",
            metadata={"path": str(path)},
        )]
