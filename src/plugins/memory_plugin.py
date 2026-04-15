"""
Abstract base class for Loca memory plugins.

A memory plugin replaces the built-in LLM-extraction memory system with
any backend that can store text and retrieve relevant snippets semantically.

Built-in implementation: BuiltinMemoryPlugin (this module)
  - Verbatim storage in existing SQLite memories table
  - Embeddings via the local model's /v1/embeddings endpoint
  - Cosine-similarity top-K retrieval; falls back to keyword search

External plugin example (future MemPalace integration):
  config.yaml:
    plugins:
      memory:
        type: external
        command: ["mempalace-server", "--port", "8090"]
        port: 8090
"""

from __future__ import annotations

import logging
import re
import struct
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import httpx

from ..store import (
    add_memory,
    delete_memory,
    get_memory_embedding,
    list_memories,
    list_memories_without_embeddings,
    set_memory_embedding,
    update_memory,
)

if TYPE_CHECKING:
    from ..inference_backend import InferenceBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class MemoryPlugin(ABC):
    """Common interface all memory backends must implement."""

    @abstractmethod
    async def store(self, text: str, metadata: dict) -> str:
        """
        Store text verbatim.  metadata keys: conv_id, type.
        Returns the memory ID.
        """

    @abstractmethod
    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        """
        Return up to `limit` memories most relevant to `query`.
        Each dict has: id, content, type, created, score (optional).
        """

    @abstractmethod
    def list_all(self, type: str | None = None) -> list[dict]:
        """Return all stored memories (optionally filtered by type)."""

    @abstractmethod
    def delete(self, mem_id: str) -> None:
        """Hard-delete a memory."""

    @abstractmethod
    def update(self, mem_id: str, content: str) -> None:
        """Update memory content (clears its embedding so it's re-embedded)."""

    def format_for_prompt(self, memories: list[dict]) -> str:
        """Format a retrieved memory list for system-prompt injection."""
        if not memories:
            return ""
        lines = "\n".join(f"- {m['content']}" for m in memories)
        return f"<memory>\nRelevant context from past conversations:\n{lines}\n</memory>"


# ---------------------------------------------------------------------------
# Built-in implementation
# ---------------------------------------------------------------------------

class BuiltinMemoryPlugin(MemoryPlugin):
    """
    Verbatim storage + local-model embeddings for semantic recall.

    Storage layer: existing SQLite memories table (extended with embedding BLOB).
    Embeddings:    POST /v1/embeddings on the local inference backend.
    Retrieval:     cosine similarity; falls back to case-insensitive keyword scan.
    """

    def __init__(self, backend: InferenceBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    async def _embed(self, text: str) -> list[float] | None:
        """
        Request an embedding from the local model.
        Returns None if the backend is not running or the request fails.
        """
        if not self._backend.is_running():
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._backend.api_base()}/v1/embeddings",
                    json={"input": text[:2048], "model": "default"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.debug(f"Embedding request failed (will use keyword fallback): {exc}")
            return None

    @staticmethod
    def _pack(embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    @staticmethod
    def _unpack(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb + 1e-8) if (na and nb) else 0.0

    # ------------------------------------------------------------------
    # MemoryPlugin interface
    # ------------------------------------------------------------------

    async def store(self, text: str, metadata: dict) -> str:
        mid = add_memory(
            content=text,
            conv_id=metadata.get("conv_id"),
            type=metadata.get("type", "user_fact"),
        )
        embedding = await self._embed(text)
        if embedding:
            set_memory_embedding(mid, self._pack(embedding))
        return mid

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        all_mems = list_memories(limit=500)
        if not all_mems:
            return []

        query_embedding = await self._embed(query)

        if query_embedding:
            scored: list[tuple[float, dict]] = []
            for m in all_mems:
                blob = get_memory_embedding(m["id"])
                if blob:
                    mem_emb = self._unpack(blob)
                    score = self._cosine(query_embedding, mem_emb)
                    scored.append((score, m))
                else:
                    scored.append((0.0, m))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [m for _, m in scored[:limit]]
        else:
            # Keyword fallback: case-insensitive word overlap
            query_words = set(re.findall(r"\w+", query.lower()))
            scored = []
            for m in all_mems:
                mem_words = set(re.findall(r"\w+", m["content"].lower()))
                overlap = len(query_words & mem_words)
                scored.append((overlap, m))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [m for _, m in scored[:limit] if scored[0][0] > 0]

    def list_all(self, type: str | None = None) -> list[dict]:
        return list_memories(limit=500, type=type)

    def delete(self, mem_id: str) -> None:
        delete_memory(mem_id)

    def update(self, mem_id: str, content: str) -> None:
        update_memory(mem_id, content)
        # Clear stale embedding so next recall re-embeds
        set_memory_embedding(mem_id, None)

    # ------------------------------------------------------------------
    # Background embedding backfill
    # ------------------------------------------------------------------

    async def backfill_embeddings(self) -> int:
        """
        Embed any memories that don't have embeddings yet.
        Called once after the inference backend becomes ready.
        Returns the count of newly embedded memories.
        """
        pending = list_memories_without_embeddings(limit=200)
        count = 0
        for m in pending:
            emb = await self._embed(m["content"])
            if emb:
                set_memory_embedding(m["id"], self._pack(emb))
                count += 1
        if count:
            logger.info(f"Memory backfill: embedded {count} existing memories")
        return count
