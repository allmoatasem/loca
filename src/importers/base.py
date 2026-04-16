from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chunk:
    text: str
    source: str       # adapter name: "anthropic", "markdown", "pdf", etc.
    title: str        # conversation name, filename, or section heading
    created_at: str   # ISO timestamp if available, "" otherwise
    metadata: dict = field(default_factory=dict)


@dataclass
class ImportResult:
    total: int     # chunks extracted by adapter
    stored: int    # chunks written to MemPalace (new)
    skipped: int   # duplicates skipped
    source: str    # adapter name that handled this import


class BaseAdapter(ABC):
    """All knowledge source adapters implement this interface."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier: 'anthropic', 'markdown', 'pdf', etc."""

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this adapter can parse the given path."""

    @abstractmethod
    def extract(self, path: Path) -> list[Chunk]:
        """Parse path and return a flat list of Chunks."""
