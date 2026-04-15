"""
MemPalace-backed memory plugin for Loca.

Stores conversation exchanges verbatim in a local MemPalace palace
(~/.mempalace/palace) and retrieves relevant memories via semantic search.

Falls back gracefully if MemPalace or ChromaDB is unavailable — Loca keeps
working, just without memory recall.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .memory_plugin import MemoryPlugin

logger = logging.getLogger(__name__)

_PALACE_PATH = Path.home() / ".mempalace" / "palace"

_DECISION_WORDS = {"decided", "decision", "went with", "chose", "because of", "we will", "agreed"}
_PREFERENCE_WORDS = {"prefer", "always", "never", "i like", "i hate", "i love", "i want", "style"}
_PROBLEM_WORDS = {"bug", "crash", "error", "failed", "broken", "issue", "exception", "traceback"}
_MILESTONE_WORDS = {"fixed", "solved", "working", "finally", "got it", "done", "completed"}


def _classify_room(text: str) -> str:
    """Assign a MemPalace room based on simple keyword matching."""
    t = text.lower()
    if any(w in t for w in _DECISION_WORDS):
        return "decisions"
    if any(w in t for w in _PREFERENCE_WORDS):
        return "preferences"
    if any(w in t for w in _PROBLEM_WORDS):
        return "problems"
    if any(w in t for w in _MILESTONE_WORDS):
        return "milestones"
    return "general"


class MemPalaceMemoryPlugin(MemoryPlugin):
    """
    Memory plugin backed by MemPalace.

    Uses MemPalace's Python API directly:
    - mempalace.searcher.get_collection  — open/create the ChromaDB collection
    - mempalace.miner.add_drawer         — store a verbatim chunk
    - mempalace.searcher.search_memories — semantic search over stored chunks
    """

    def __init__(self) -> None:
        self._available = False
        self._palace_path = str(_PALACE_PATH)
        self._collection = None
        self._try_init()

    def _try_init(self) -> None:
        try:
            from mempalace.searcher import get_collection  # noqa: PLC0415
            _PALACE_PATH.mkdir(parents=True, exist_ok=True)
            self._collection = get_collection(self._palace_path)
            self._available = True
            logger.info("MemPalace memory plugin ready at %s", self._palace_path)
        except ImportError:
            logger.warning(
                "MemPalace not installed — memory disabled. "
                "Install with: pip install mempalace 'chromadb>=1.5.4'"
            )
        except Exception as exc:
            logger.warning("MemPalace failed to initialise (%s) — memory disabled.", exc)

    # ------------------------------------------------------------------
    # MemoryPlugin interface
    # ------------------------------------------------------------------

    async def store(self, text: str, metadata: dict) -> str:
        if not self._available or not text.strip():
            return ""
        try:
            from mempalace.miner import add_drawer  # noqa: PLC0415
            source = "loca-chat"
            chunk_index = 0  # each store() call is treated as a fresh single-chunk document
            add_drawer(
                self._collection,
                wing="loca",
                room=_classify_room(text),
                content=text,
                source_file=source,
                chunk_index=chunk_index,
                agent="loca",
            )
            # MemPalace assigns its own internal ID; we cannot recover it from add_drawer.
            # Callers must use list_all() to obtain IDs for delete/update.
            return ""
        except Exception as exc:
            logger.warning("MemPalace store failed: %s", exc)
            return ""

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        if not self._available or not query.strip():
            return []
        try:
            from mempalace.searcher import search_memories  # noqa: PLC0415
            result = search_memories(
                query,
                palace_path=self._palace_path,
                wing="loca",
                n_results=limit,
            )
            return [
                {
                    "id": r.get("source_file", ""),
                    "content": r["text"],
                    "type": r.get("room", "general"),
                    "created": "",
                    "score": round(1.0 - r.get("distance", 1.0), 4),
                }
                for r in result.get("results", [])
            ]
        except Exception as exc:
            logger.warning("MemPalace recall failed: %s", exc)
            return []

    def list_all(self, type: str | None = None) -> list[dict]:
        if not self._available or self._collection is None:
            return []
        try:
            where: dict = {"wing": "loca"}
            if type:
                where["room"] = type
            result = self._collection.get(
                where=where,
                limit=200,
                include=["documents", "metadatas"],
            )
            return [
                {
                    "id": doc_id,
                    "content": result["documents"][i] if result.get("documents") and i < len(result["documents"]) else "",
                    "type": (result["metadatas"][i].get("room", "general") if result.get("metadatas") and i < len(result["metadatas"]) else "general"),
                    "created": (result["metadatas"][i].get("timestamp", "") if result.get("metadatas") and i < len(result["metadatas"]) else ""),
                }
                for i, doc_id in enumerate(result.get("ids", []))
            ]
        except Exception as exc:
            logger.warning("MemPalace list_all failed: %s", exc)
            return []

    def delete(self, mem_id: str) -> None:
        if not self._available or self._collection is None:
            return
        try:
            self._collection.delete(ids=[mem_id])
        except Exception as exc:
            logger.warning("MemPalace delete failed: %s", exc)

    def update(self, mem_id: str, content: str) -> None:
        if not self._available or self._collection is None:
            return
        try:
            self._collection.update(ids=[mem_id], documents=[content])
        except Exception as exc:
            logger.warning("MemPalace update failed: %s", exc)
