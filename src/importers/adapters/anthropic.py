from __future__ import annotations

import json
import re
from pathlib import Path

from ..base import BaseAdapter, Chunk


def _extract_text(message: dict) -> str:
    """Extract plain text from a chat_message dict."""
    content = message.get("content", [])
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        text = " ".join(parts).strip()
        if text:
            return text
    return str(message.get("text", "")).strip()


def _chunk_markdown(text: str) -> list[str]:
    """Split markdown by ## headings. Returns whole text if no headings found."""
    sections = re.split(r"(?m)^##+ ", text)
    sections = [s.strip() for s in sections if s.strip()]
    return sections if sections else [text.strip()]


class AnthropicAdapter(BaseAdapter):
    source_name = "anthropic"

    def can_handle(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        conversations = path / "conversations.json"
        if not conversations.exists():
            return False
        try:
            data = json.loads(conversations.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return "chat_messages" in data[0]
            return isinstance(data, list)
        except Exception:
            return False

    def extract(self, path: Path) -> list[Chunk]:
        chunks: list[Chunk] = []

        conv_file = path / "conversations.json"
        if conv_file.exists():
            conversations = json.loads(conv_file.read_text(encoding="utf-8"))
            for conv in conversations:
                messages = conv.get("chat_messages", [])
                title = conv.get("name", "Untitled")
                created_at = conv.get("created_at", "")
                i = 0
                while i < len(messages):
                    msg = messages[i]
                    if msg.get("sender") == "human":
                        user_text = _extract_text(msg)
                        assistant_text = ""
                        if i + 1 < len(messages) and messages[i + 1].get("sender") == "assistant":
                            assistant_text = _extract_text(messages[i + 1])
                            i += 1
                        text = f"User: {user_text}"
                        if assistant_text:
                            text += f"\n\nAssistant: {assistant_text}"
                        if text.strip():
                            chunks.append(Chunk(
                                text=text,
                                source="anthropic",
                                title=title,
                                created_at=created_at,
                                metadata={
                                    "type": "conversation",
                                    "conv_id": conv.get("uuid", ""),
                                },
                            ))
                    i += 1

        mem_file = path / "memories.json"
        if mem_file.exists():
            memories = json.loads(mem_file.read_text(encoding="utf-8"))
            for entry in memories:
                raw = entry.get("conversations_memory", "")
                if isinstance(raw, str) and raw.strip():
                    for section in _chunk_markdown(raw):
                        chunks.append(Chunk(
                            text=section,
                            source="anthropic",
                            title="Claude memory",
                            created_at="",
                            metadata={"type": "memory"},
                        ))

        proj_file = path / "projects.json"
        if proj_file.exists():
            projects = json.loads(proj_file.read_text(encoding="utf-8"))
            for project in projects:
                proj_name = project.get("name", "Unknown project")
                for doc in project.get("docs", []):
                    content = doc.get("content", "").strip()
                    if content:
                        chunks.append(Chunk(
                            text=content,
                            source="anthropic",
                            title=f"{proj_name} — {doc.get('filename', 'doc')}",
                            created_at=doc.get("created_at", ""),
                            metadata={
                                "type": "project_doc",
                                "project": proj_name,
                            },
                        ))

        return chunks
