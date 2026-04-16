from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from mempalace.miner import add_drawer  # type: ignore
except Exception:  # MemPalace optional at import time
    def add_drawer(*_args, **_kwargs):  # type: ignore
        raise ImportError("mempalace not available")

from ..plugins.mempalace_plugin import _classify_room
from ..store import add_import_record
from .base import BaseAdapter, Chunk

if TYPE_CHECKING:
    from ..plugins.mempalace_plugin import MemPalaceMemoryPlugin

logger = logging.getLogger(__name__)

_MAX_WORDS = 800  # chunks exceeding this are split further


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _split_large_chunk(chunk: Chunk) -> list[Chunk]:
    """Split a chunk exceeding _MAX_WORDS into ~500-word pieces with 50-word overlap."""
    words = chunk.text.split()
    if len(words) <= _MAX_WORDS:
        return [chunk]
    step = 500
    overlap = 50
    pieces: list[Chunk] = []
    i = 0
    while i < len(words):
        piece_words = words[i : i + step]
        pieces.append(
            Chunk(
                text=" ".join(piece_words),
                source=chunk.source,
                title=chunk.title,
                created_at=chunk.created_at,
                metadata={**chunk.metadata, "part": len(pieces)},
            )
        )
        i += step - overlap
    return pieces


class ImportService:
    def __init__(self, memory_plugin: "MemPalaceMemoryPlugin") -> None:
        self._plugin = memory_plugin
        self._adapters: list[BaseAdapter] = []

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters.append(adapter)

    def _detect(self, path: Path) -> BaseAdapter | None:
        for adapter in self._adapters:
            if adapter.can_handle(path):
                return adapter
        return None

    def _is_duplicate(self, content_hash: str) -> bool:
        """Check MemPalace collection for existing chunk with this hash."""
        if self._plugin.collection is None:
            return False
        try:
            result = self._plugin.collection.get(
                where={"content_hash": content_hash},
                include=[],
            )
            return len(result.get("ids", [])) > 0
        except Exception:
            return False

    async def run(self, path: "Path | str"):
        path = Path(path).expanduser().resolve()
        yield {"status": "detecting", "path": str(path)}

        adapter = self._detect(path)
        if adapter is None:
            yield {"status": "error", "message": f"No adapter found for: {path}"}
            return

        try:
            raw_chunks = adapter.extract(path)
        except Exception as exc:
            yield {"status": "error", "message": str(exc)}
            return

        # Second-pass: split oversized chunks (conversation chunks never split)
        chunks: list[Chunk] = []
        for chunk in raw_chunks:
            if chunk.source in ("anthropic", "openai") and chunk.metadata.get("type") == "conversation":
                chunks.append(chunk)
            else:
                chunks.extend(_split_large_chunk(chunk))

        yield {"status": "extracting", "adapter": adapter.source_name, "total": len(chunks)}

        stored = 0
        skipped = 0

        for i, chunk in enumerate(chunks):
            h = _content_hash(chunk.text)
            if self._is_duplicate(h):
                skipped += 1
                yield {"status": "progress", "current": i + 1, "total": len(chunks), "skipped": skipped}
                continue

            try:
                add_drawer(
                    self._plugin.collection,
                    wing="loca",
                    room=_classify_room(chunk.text),
                    content=chunk.text,
                    source_file=f"{chunk.source}:{chunk.title}",
                    chunk_index=i,
                    agent="loca-import",
                )
                stored += 1
            except Exception as exc:
                logger.warning("Failed to store chunk %d: %s", i, exc)

            yield {"status": "progress", "current": i + 1, "total": len(chunks), "skipped": skipped}

        add_import_record(
            source=adapter.source_name,
            path=str(path),
            stored=stored,
            skipped=skipped,
        )

        yield {"status": "done", "total": len(chunks), "stored": stored, "skipped": skipped}
