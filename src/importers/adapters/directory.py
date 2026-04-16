from __future__ import annotations

import logging
from pathlib import Path

from ..base import BaseAdapter, Chunk

logger = logging.getLogger(__name__)

# Filesystem names that should never be descended into
_SKIP_PATTERNS = {".git", "__pycache__", ".DS_Store", "node_modules"}


class DirectoryAdapter(BaseAdapter):
    """Recursively walks a directory, delegating each file to a child adapter."""

    source_name = "directory"

    def __init__(self, adapters: list[BaseAdapter] | None = None) -> None:
        self._child_adapters = adapters or []

    def can_handle(self, path: Path) -> bool:
        return path.is_dir()

    def extract(self, path: Path) -> list[Chunk]:
        chunks: list[Chunk] = []
        for file_path in sorted(path.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part in _SKIP_PATTERNS for part in file_path.parts):
                continue
            matched = False
            for adapter in self._child_adapters:
                if adapter.can_handle(file_path):
                    try:
                        chunks.extend(adapter.extract(file_path))
                    except Exception as exc:
                        logger.warning("Adapter %s failed on %s: %s",
                                       adapter.source_name, file_path.name, exc)
                    matched = True
                    break
            if not matched:
                logger.debug("No adapter for %s — skipping", file_path.name)
        return chunks
