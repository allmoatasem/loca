from __future__ import annotations

import json
from pathlib import Path

from ..base import BaseAdapter, Chunk


def _walk_mapping(mapping: dict) -> list[tuple[str, str]]:
    """Reconstruct ordered (role, text) pairs from a ChatGPT export mapping tree."""
    root = next((v for v in mapping.values() if v.get("parent") is None), None)
    if root is None:
        return []

    messages: list[tuple[str, str]] = []

    def _visit(node_id: str) -> None:
        node = mapping.get(node_id, {})
        msg = node.get("message") or {}
        role = msg.get("author", {}).get("role", "")
        parts = msg.get("content", {}).get("parts", [])
        text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
        if role in ("user", "assistant") and text:
            messages.append((role, text))
        for child_id in node.get("children", []):
            _visit(child_id)

    for child_id in root.get("children", []):
        _visit(child_id)

    return messages


class OpenAIAdapter(BaseAdapter):
    source_name = "openai"

    def can_handle(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        conv_file = path / "conversations.json"
        if not conv_file.exists():
            return False
        try:
            data = json.loads(conv_file.read_text(encoding="utf-8"))
            return isinstance(data, list) and bool(data) and "mapping" in data[0]
        except Exception:
            return False

    def extract(self, path: Path) -> list[Chunk]:
        data = json.loads((path / "conversations.json").read_text(encoding="utf-8"))
        chunks: list[Chunk] = []
        for conv in data:
            title = conv.get("title", "Untitled")
            created_at = str(conv.get("create_time", ""))
            messages = _walk_mapping(conv.get("mapping", {}))
            i = 0
            while i < len(messages):
                role, text = messages[i]
                if role == "user":
                    assistant_text = ""
                    if i + 1 < len(messages) and messages[i + 1][0] == "assistant":
                        assistant_text = messages[i + 1][1]
                        i += 1
                    combined = f"User: {text}"
                    if assistant_text:
                        combined += f"\n\nAssistant: {assistant_text}"
                    chunks.append(Chunk(
                        text=combined,
                        source="openai",
                        title=title,
                        created_at=created_at,
                        metadata={"type": "conversation", "conv_id": conv.get("id", "")},
                    ))
                i += 1
        return chunks
