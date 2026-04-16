# Knowledge Import Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-agnostic knowledge ingestion pipeline that imports conversations, documents, and files from any supported source into MemPalace, available via CLI (`make import path=...`) and a Settings panel UI in both Swift and HTML.

**Architecture:** A `src/importers/` package with a `BaseAdapter` ABC, a central `ImportService` that handles format detection, deduplication (SHA-256), chunking, and MemPalace storage via SSE-streamed progress. Eleven adapters cover Anthropic exports, ChatGPT exports, markdown, PDF, EPUB, images, spreadsheets, JSON, DOCX, web URLs, and directories. The CLI and a `/api/import` SSE endpoint share the same ImportService.

**Tech Stack:** Python (FastAPI, asyncio), MemPalace/ChromaDB, SQLite (import_history table), pypdf, trafilatura, pandas, openpyxl, python-docx, ebooklib, SwiftUI (NSOpenPanel + URLSession SSE), HTML/JS (EventSource).

---

## File Map

**Create:**
- `src/importers/__init__.py`
- `src/importers/base.py` — `Chunk`, `ImportResult`, `BaseAdapter` ABC
- `src/importers/service.py` — `ImportService`: registry, detection, dedup, chunking, storage, progress
- `src/importers/cli.py` — CLI entry point (`python -m src.importers.cli`)
- `src/importers/adapters/__init__.py`
- `src/importers/adapters/anthropic.py` — Anthropic JSON export
- `src/importers/adapters/openai.py` — ChatGPT JSON export
- `src/importers/adapters/markdown.py` — `.md`, `.txt`, `.rst`
- `src/importers/adapters/pdf.py` — `.pdf` via pypdf
- `src/importers/adapters/epub.py` — `.epub` via ebooklib
- `src/importers/adapters/image.py` — images via mlx-vlm OCR
- `src/importers/adapters/spreadsheet.py` — `.csv`, `.xlsx`
- `src/importers/adapters/json_adapter.py` — generic `.json`
- `src/importers/adapters/docx.py` — `.docx` via python-docx
- `src/importers/adapters/web.py` — URLs via trafilatura
- `src/importers/adapters/directory.py` — recursive folder walker
- `tests/test_importers/__init__.py`
- `tests/test_importers/test_base.py`
- `tests/test_importers/test_service.py`
- `tests/test_importers/test_adapters.py`
- `tests/fixtures/anthropic_export/conversations.json`
- `tests/fixtures/anthropic_export/memories.json`
- `tests/fixtures/anthropic_export/projects.json`
- `tests/fixtures/anthropic_export/users.json`
- `tests/fixtures/sample.md`

**Modify:**
- `src/store.py` — add `import_history` table to `_migrate()`, add `add_import_record()` and `list_import_history()`
- `src/proxy.py` — add `POST /api/import` (SSE) and `GET /api/import/history`
- `src/plugins/mempalace_plugin.py` — expose `collection` and `palace_path` as public properties
- `requirements.txt` — add `openpyxl`, `python-docx`, `ebooklib`
- `Makefile` — add `import` target
- `Loca-SwiftUI/Sources/Loca/Backend/Models.swift` — add `ImportHistoryItem` struct
- `Loca-SwiftUI/Sources/Loca/Backend/BackendClient.swift` — add `importKnowledge()` and `fetchImportHistory()`
- `Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift` — add "Import Knowledge" section
- `src/static/index.html` — add import section in settings panel

---

## Task 1: Core types (`base.py`)

**Files:**
- Create: `src/importers/__init__.py`
- Create: `src/importers/base.py`
- Create: `tests/test_importers/__init__.py`
- Create: `tests/test_importers/test_base.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_importers/test_base.py
from pathlib import Path
from src.importers.base import Chunk, ImportResult, BaseAdapter


def test_chunk_defaults():
    c = Chunk(text="hello", source="test", title="t", created_at="", metadata={})
    assert c.text == "hello"
    assert c.source == "test"
    assert c.metadata == {}


def test_import_result_fields():
    r = ImportResult(total=10, stored=8, skipped=2, source="test")
    assert r.total == 10
    assert r.stored + r.skipped == r.total


def test_base_adapter_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        BaseAdapter()  # cannot instantiate abstract class


def test_concrete_adapter_must_implement_all_methods():
    class Bad(BaseAdapter):
        pass  # missing all three methods
    import pytest
    with pytest.raises(TypeError):
        Bad()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_importers/test_base.py -v
```
Expected: `ImportError: cannot import name 'Chunk'`

- [ ] **Step 3: Create the package files**

```python
# src/importers/__init__.py
# (empty)
```

```python
# src/importers/base.py
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
```

```python
# tests/test_importers/__init__.py
# (empty)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_importers/test_base.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/importers/__init__.py src/importers/base.py \
        tests/test_importers/__init__.py tests/test_importers/test_base.py
git commit -m "feat: add knowledge import base types (Chunk, ImportResult, BaseAdapter)"
```

---

## Task 2: SQLite import history

**Files:**
- Modify: `src/store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_importers/test_base.py`:

```python
def test_add_and_list_import_record():
    from src.store import add_import_record, list_import_history
    add_import_record(source="anthropic", path="/tmp/export", stored=10, skipped=2)
    history = list_import_history()
    assert any(r["source"] == "anthropic" and r["stored"] == 10 for r in history)
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_importers/test_base.py::test_add_and_list_import_record -v
```
Expected: `ImportError: cannot import name 'add_import_record'`

- [ ] **Step 3: Add import_history table and functions to `src/store.py`**

In `_migrate()`, add after the vault analyser block:

```python
    c.executescript("""
    CREATE TABLE IF NOT EXISTS import_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        path        TEXT NOT NULL,
        stored      INTEGER NOT NULL,
        skipped     INTEGER NOT NULL,
        imported_at TEXT NOT NULL
    );
    """)
```

Add these two functions anywhere after the existing memory functions:

```python
def add_import_record(source: str, path: str, stored: int, skipped: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO import_history (source, path, stored, skipped, imported_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, path, stored, skipped, _utcnow()),
        )
        c.commit()


def list_import_history(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT source, path, stored, skipped, imported_at "
            "FROM import_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
```

Check whether `_utcnow()` already exists in `store.py`:

```bash
grep -n "_utcnow\|utcnow\|datetime" src/store.py | head -10
```

If `_utcnow` does not exist, add this helper near the top of `store.py` (after imports):

```python
import datetime

def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
```

If a similar helper already exists, use it instead.

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest tests/test_importers/test_base.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_importers/test_base.py
git commit -m "feat: add import_history SQLite table and store helpers"
```

---

## Task 3: Expose MemPalace collection on the plugin

**Files:**
- Modify: `src/plugins/mempalace_plugin.py`

The `ImportService` needs direct access to the MemPalace ChromaDB collection and palace path to call `add_drawer` and check for existing content hashes. Currently `_collection` and `_palace_path` are private.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mempalace_plugin.py` (existing file):

```python
def test_plugin_exposes_collection_and_palace_path():
    plugin = self._make_plugin()  # uses existing helper
    assert plugin.collection is not None
    assert isinstance(plugin.palace_path, str)
    assert "palace" in plugin.palace_path
```

Since the test class uses `setup_method`, add as a standalone test instead:

```python
def test_plugin_exposes_collection_and_palace_path():
    import sys
    from unittest.mock import MagicMock, patch
    collection = MagicMock()
    fake_searcher = MagicMock()
    fake_searcher.get_collection.return_value = collection
    fake_miner = MagicMock()
    sys.modules["mempalace"] = MagicMock()
    sys.modules["mempalace.searcher"] = fake_searcher
    sys.modules["mempalace.miner"] = fake_miner
    from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
    with patch("src.plugins.mempalace_plugin._PALACE_PATH") as mock_path:
        mock_path.exists.return_value = True
        mock_path.__str__ = lambda s: "/fake/palace"
        plugin = MemPalaceMemoryPlugin()
    assert plugin.collection is collection
    assert plugin.palace_path == "/fake/palace"
    for key in list(sys.modules.keys()):
        if key.startswith("mempalace"):
            del sys.modules[key]
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_mempalace_plugin.py::test_plugin_exposes_collection_and_palace_path -v
```
Expected: `AttributeError: 'MemPalaceMemoryPlugin' object has no attribute 'collection'`

- [ ] **Step 3: Add public properties to `src/plugins/mempalace_plugin.py`**

Add after `_try_init()`:

```python
    @property
    def collection(self):
        """The underlying ChromaDB collection. None if MemPalace unavailable."""
        return self._collection

    @property
    def palace_path(self) -> str:
        """Path to the MemPalace palace directory."""
        return self._palace_path
```

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest tests/test_mempalace_plugin.py -v
```
Expected: all 18 pass

- [ ] **Step 5: Commit**

```bash
git add src/plugins/mempalace_plugin.py tests/test_mempalace_plugin.py
git commit -m "feat: expose collection and palace_path properties on MemPalaceMemoryPlugin"
```

---

## Task 4: ImportService

**Files:**
- Create: `src/importers/service.py`
- Create: `tests/test_importers/test_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_importers/test_service.py
from __future__ import annotations
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from src.importers.base import Chunk, BaseAdapter
from src.importers.service import ImportService


class FakeAdapter(BaseAdapter):
    source_name = "fake"

    def can_handle(self, path: Path) -> bool:
        return path.suffix == ".fake"

    def extract(self, path: Path) -> list[Chunk]:
        return [Chunk(text="hello world", source="fake", title="test", created_at="", metadata={})]


def _make_service(collection=None):
    plugin = MagicMock()
    plugin.collection = collection or MagicMock()
    plugin.palace_path = "/fake/palace"
    plugin._available = True
    svc = ImportService(memory_plugin=plugin)
    svc.register(FakeAdapter())
    return svc


def test_register_adapter():
    svc = _make_service()
    assert any(a.source_name == "fake" for a in svc._adapters)


def test_detect_adapter_by_extension(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    svc = _make_service()
    adapter = svc._detect(f)
    assert adapter is not None
    assert adapter.source_name == "fake"


def test_detect_returns_none_for_unknown(tmp_path):
    f = tmp_path / "data.xyz"
    f.write_text("x")
    svc = _make_service()
    assert svc._detect(f) is None


def test_content_hash_is_sha256():
    from src.importers.service import _content_hash
    text = "hello"
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert _content_hash(text) == expected


@pytest.mark.asyncio
async def test_run_yields_progress_and_done(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    collection = MagicMock()
    collection.get.return_value = {"ids": []}  # no duplicates
    svc = _make_service(collection=collection)
    events = []
    with patch("src.importers.service.add_drawer"), \
         patch("src.importers.service.add_import_record"):
        async for event in svc.run(f):
            events.append(event)
    statuses = [e["status"] for e in events]
    assert "extracting" in statuses
    assert events[-1]["status"] == "done"
    assert events[-1]["stored"] >= 0


@pytest.mark.asyncio
async def test_duplicate_chunk_is_skipped(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    collection = MagicMock()
    # Simulate existing hash in MemPalace
    import hashlib
    existing_hash = hashlib.sha256("hello world".encode()).hexdigest()
    collection.get.return_value = {"ids": ["existing-id"]}
    svc = _make_service(collection=collection)
    events = []
    with patch("src.importers.service.add_drawer") as mock_add, \
         patch("src.importers.service.add_import_record"):
        async for event in svc.run(f):
            events.append(event)
    mock_add.assert_not_called()
    done = next(e for e in events if e["status"] == "done")
    assert done["skipped"] == 1
    assert done["stored"] == 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_service.py -v
```
Expected: `ImportError: cannot import name 'ImportService'`

- [ ] **Step 3: Implement `src/importers/service.py`**

```python
# src/importers/service.py
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator, TYPE_CHECKING

from .base import BaseAdapter, Chunk, ImportResult
from ..store import add_import_record

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
    pieces = []
    i = 0
    while i < len(words):
        piece_words = words[i: i + step]
        pieces.append(Chunk(
            text=" ".join(piece_words),
            source=chunk.source,
            title=chunk.title,
            created_at=chunk.created_at,
            metadata={**chunk.metadata, "part": len(pieces)},
        ))
        i += step - overlap
    return pieces


class ImportService:
    def __init__(self, memory_plugin: MemPalaceMemoryPlugin) -> None:
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

    async def run(self, path: Path | str) -> AsyncIterator[dict]:
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

        try:
            from mempalace.miner import add_drawer  # noqa: PLC0415
        except ImportError:
            yield {"status": "error", "message": "MemPalace not available"}
            return

        from ..plugins.mempalace_plugin import _classify_room  # noqa: PLC0415

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
```

- [ ] **Step 4: Run to verify tests pass**

```bash
python -m pytest tests/test_importers/test_service.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/importers/service.py tests/test_importers/test_service.py
git commit -m "feat: add ImportService with dedup, chunking, SSE progress"
```

---

## Task 5: AnthropicAdapter + test fixtures

**Files:**
- Create: `src/importers/adapters/__init__.py`
- Create: `src/importers/adapters/anthropic.py`
- Create: `tests/fixtures/anthropic_export/conversations.json`
- Create: `tests/fixtures/anthropic_export/memories.json`
- Create: `tests/fixtures/anthropic_export/projects.json`
- Create: `tests/fixtures/anthropic_export/users.json`
- Create: `tests/test_importers/test_adapters.py`

- [ ] **Step 1: Create test fixtures**

```json
// tests/fixtures/anthropic_export/conversations.json
[
  {
    "uuid": "conv-001",
    "name": "Test conversation",
    "summary": "A test.",
    "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-01T10:05:00Z",
    "account": {},
    "chat_messages": [
      {
        "uuid": "msg-001",
        "sender": "human",
        "content": [{"type": "text", "text": "Hello Claude"}],
        "text": "Hello Claude",
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T10:00:00Z",
        "attachments": [],
        "files": []
      },
      {
        "uuid": "msg-002",
        "sender": "assistant",
        "content": [{"type": "text", "text": "Hello! How can I help?"}],
        "text": "Hello! How can I help?",
        "created_at": "2026-01-01T10:00:05Z",
        "updated_at": "2026-01-01T10:00:05Z",
        "attachments": [],
        "files": []
      }
    ]
  }
]
```

```json
// tests/fixtures/anthropic_export/memories.json
[{"conversations_memory": "## Preferences\n\nUser prefers Python.\n\n## Work\n\nUser works at JLR.", "project_memories": [], "account_uuid": "test"}]
```

```json
// tests/fixtures/anthropic_export/projects.json
[{"uuid": "proj-001", "name": "Test Project", "description": "A test project.", "is_private": false, "is_starter_project": false, "prompt_template": "", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "creator": {}, "docs": [{"uuid": "doc-001", "filename": "notes.md", "content": "# Notes\n\nSome project notes.", "created_at": "2026-01-01T00:00:00Z"}]}]
```

```json
// tests/fixtures/anthropic_export/users.json
[{"uuid": "user-001", "full_name": "Test User"}]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_importers/test_adapters.py
from pathlib import Path
import pytest
from src.importers.adapters.anthropic import AnthropicAdapter

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "anthropic_export"


def test_anthropic_can_handle_export_dir():
    adapter = AnthropicAdapter()
    assert adapter.can_handle(FIXTURE_DIR) is True


def test_anthropic_cannot_handle_single_file(tmp_path):
    f = tmp_path / "conversations.json"
    f.write_text("[]")
    adapter = AnthropicAdapter()
    assert adapter.can_handle(f) is False  # file not dir


def test_anthropic_extracts_conversation_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    conv_chunks = [c for c in chunks if c.metadata.get("type") == "conversation"]
    assert len(conv_chunks) >= 1
    assert "Hello Claude" in conv_chunks[0].text
    assert "Hello! How can I help?" in conv_chunks[0].text


def test_anthropic_extracts_memory_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    mem_chunks = [c for c in chunks if c.metadata.get("type") == "memory"]
    assert len(mem_chunks) >= 1
    assert any("Preferences" in c.text or "Python" in c.text for c in mem_chunks)


def test_anthropic_extracts_project_doc_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    doc_chunks = [c for c in chunks if c.metadata.get("type") == "project_doc"]
    assert len(doc_chunks) >= 1
    assert "project notes" in doc_chunks[0].text.lower()


def test_anthropic_source_name():
    assert AnthropicAdapter().source_name == "anthropic"
```

- [ ] **Step 3: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: `ImportError: cannot import name 'AnthropicAdapter'`

- [ ] **Step 4: Implement `src/importers/adapters/__init__.py` and `src/importers/adapters/anthropic.py`**

```python
# src/importers/adapters/__init__.py
# (empty)
```

```python
# src/importers/adapters/anthropic.py
from __future__ import annotations

import json
import re
from pathlib import Path

from ..base import BaseAdapter, Chunk


def _extract_text(message: dict) -> str:
    """Extract plain text from a chat_message dict."""
    # Try content blocks first (list of {type, text})
    content = message.get("content", [])
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        text = " ".join(parts).strip()
        if text:
            return text
    # Fallback to top-level text field
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
            # Distinguish from OpenAI export which uses a "mapping" key
            if isinstance(data, list) and data:
                return "chat_messages" in data[0]
            return isinstance(data, list)  # empty list — assume Anthropic
        except Exception:
            return False

    def extract(self, path: Path) -> list[Chunk]:
        chunks: list[Chunk] = []

        # 1. Conversations
        conv_file = path / "conversations.json"
        if conv_file.exists():
            conversations = json.loads(conv_file.read_text(encoding="utf-8"))
            for conv in conversations:
                messages = conv.get("chat_messages", [])
                title = conv.get("name", "Untitled")
                created_at = conv.get("created_at", "")
                # Pair human + assistant messages into single chunks
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

        # 2. Memories
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

        # 3. Project docs
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
```

- [ ] **Step 5: Run to verify tests pass**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: all 6 pass

- [ ] **Step 6: Commit**

```bash
git add src/importers/adapters/__init__.py src/importers/adapters/anthropic.py \
        tests/test_importers/test_adapters.py \
        tests/fixtures/anthropic_export/
git commit -m "feat: add AnthropicAdapter for conversations, memories, project docs"
```

---

## Task 6: MarkdownAdapter + OpenAIAdapter

**Files:**
- Create: `src/importers/adapters/markdown.py`
- Create: `src/importers/adapters/openai.py`
- Create: `tests/fixtures/sample.md`

- [ ] **Step 1: Create fixture and write failing tests**

```markdown
<!-- tests/fixtures/sample.md -->
# Main Title

Intro paragraph with some content.

## Section One

Content of section one goes here. This is a test paragraph.

## Section Two

Content of section two goes here. Another test paragraph.
```

Add to `tests/test_importers/test_adapters.py`:

```python
from src.importers.adapters.markdown import MarkdownAdapter

SAMPLE_MD = Path(__file__).parent.parent / "fixtures" / "sample.md"


def test_markdown_can_handle_md_file():
    assert MarkdownAdapter().can_handle(SAMPLE_MD) is True


def test_markdown_cannot_handle_dir(tmp_path):
    assert MarkdownAdapter().can_handle(tmp_path) is False


def test_markdown_cannot_handle_pdf(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF")
    assert MarkdownAdapter().can_handle(f) is False


def test_markdown_chunks_by_heading():
    chunks = MarkdownAdapter().extract(SAMPLE_MD)
    assert len(chunks) >= 2
    texts = " ".join(c.text for c in chunks)
    assert "Section One" in texts
    assert "Section Two" in texts


def test_markdown_fallback_no_headings(tmp_path):
    f = tmp_path / "flat.md"
    f.write_text("No headings here. Just a flat block of text.")
    chunks = MarkdownAdapter().extract(f)
    assert len(chunks) == 1
    assert "flat block" in chunks[0].text


def test_openai_can_handle_export_with_mapping(tmp_path):
    from src.importers.adapters.openai import OpenAIAdapter
    (tmp_path / "conversations.json").write_text(
        '[{"id": "c1", "title": "t", "mapping": {}, "create_time": 0}]'
    )
    assert OpenAIAdapter().can_handle(tmp_path) is True


def test_openai_cannot_handle_anthropic_export():
    from src.importers.adapters.openai import OpenAIAdapter
    assert OpenAIAdapter().can_handle(FIXTURE_DIR) is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: failures on markdown and openai tests

- [ ] **Step 3: Implement `src/importers/adapters/markdown.py`**

```python
# src/importers/adapters/markdown.py
from __future__ import annotations

import re
from pathlib import Path

from ..base import BaseAdapter, Chunk

_SUPPORTED = {".md", ".txt", ".rst"}


class MarkdownAdapter(BaseAdapter):
    source_name = "markdown"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _SUPPORTED

    def extract(self, path: Path) -> list[Chunk]:
        text = path.read_text(encoding="utf-8", errors="replace")
        sections = self._split(text)
        return [
            Chunk(
                text=s.strip(),
                source="markdown",
                title=path.name,
                created_at="",
                metadata={"path": str(path)},
            )
            for s in sections if s.strip()
        ]

    def _split(self, text: str) -> list[str]:
        """Split on ## or # headings. Fall back to whole text if none found."""
        parts = re.split(r"(?m)^#{1,3} ", text)
        parts = [p.strip() for p in parts if p.strip()]
        return parts if len(parts) > 1 else [text]
```

- [ ] **Step 4: Implement `src/importers/adapters/openai.py`**

```python
# src/importers/adapters/openai.py
from __future__ import annotations

import json
from pathlib import Path

from ..base import BaseAdapter, Chunk


def _walk_mapping(mapping: dict) -> list[tuple[str, str]]:
    """Reconstruct ordered messages from ChatGPT export mapping tree."""
    # Find root node (no parent)
    root = next((v for v in mapping.values() if v.get("parent") is None), None)
    if root is None:
        return []

    messages = []

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
```

- [ ] **Step 5: Run to verify all pass**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/importers/adapters/markdown.py src/importers/adapters/openai.py \
        tests/fixtures/sample.md tests/test_importers/test_adapters.py
git commit -m "feat: add MarkdownAdapter and OpenAIAdapter"
```

---

## Task 7: Document adapters (PDF, EPUB, DOCX) + dependencies

**Files:**
- Create: `src/importers/adapters/pdf.py`
- Create: `src/importers/adapters/epub.py`
- Create: `src/importers/adapters/docx.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add new dependencies to `requirements.txt`**

Add these three lines to `requirements.txt`:
```
openpyxl>=3.1.0
python-docx>=1.1.0
ebooklib>=0.18
```

Install them:
```bash
pip install openpyxl python-docx ebooklib
```

- [ ] **Step 2: Write failing tests**

Add to `tests/test_importers/test_adapters.py`:

```python
def test_pdf_can_handle(tmp_path):
    from src.importers.adapters.pdf import PDFAdapter
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert PDFAdapter().can_handle(f) is True


def test_pdf_cannot_handle_md(tmp_path):
    from src.importers.adapters.pdf import PDFAdapter
    f = tmp_path / "doc.md"
    f.write_text("hello")
    assert PDFAdapter().can_handle(f) is False


def test_epub_can_handle(tmp_path):
    from src.importers.adapters.epub import EpubAdapter
    f = tmp_path / "book.epub"
    f.write_bytes(b"PK")
    assert EpubAdapter().can_handle(f) is True


def test_docx_can_handle(tmp_path):
    from src.importers.adapters.docx import DocxAdapter
    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK")
    assert DocxAdapter().can_handle(f) is True
```

- [ ] **Step 3: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_adapters.py::test_pdf_can_handle -v
```
Expected: `ImportError: cannot import name 'PDFAdapter'`

- [ ] **Step 4: Implement `src/importers/adapters/pdf.py`**

```python
# src/importers/adapters/pdf.py
from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class PDFAdapter(BaseAdapter):
    source_name = "pdf"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".pdf"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            from pypdf import PdfReader  # noqa: PLC0415
        except ImportError:
            return []
        reader = PdfReader(str(path))
        chunks = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(Chunk(
                    text=text,
                    source="pdf",
                    title=f"{path.name} — page {i + 1}",
                    created_at="",
                    metadata={"path": str(path), "page": i + 1},
                ))
        return chunks
```

- [ ] **Step 5: Implement `src/importers/adapters/epub.py`**

```python
# src/importers/adapters/epub.py
from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class EpubAdapter(BaseAdapter):
    source_name = "epub"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".epub"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            import ebooklib  # noqa: PLC0415
            from ebooklib import epub  # noqa: PLC0415
            from html.parser import HTMLParser  # noqa: PLC0415
        except ImportError:
            return []

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []
            def handle_data(self, data: str) -> None:
                self.parts.append(data)
            def get_text(self) -> str:
                return " ".join(self.parts).strip()

        book = epub.read_epub(str(path))
        chunks = []
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
```

- [ ] **Step 6: Implement `src/importers/adapters/docx.py`**

```python
# src/importers/adapters/docx.py
from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class DocxAdapter(BaseAdapter):
    source_name = "docx"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() == ".docx"

    def extract(self, path: Path) -> list[Chunk]:
        try:
            from docx import Document  # noqa: PLC0415
        except ImportError:
            return []
        doc = Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return []
        return [Chunk(
            text="\n".join(paragraphs),
            source="docx",
            title=path.name,
            created_at="",
            metadata={"path": str(path)},
        )]
```

- [ ] **Step 7: Run to verify tests pass**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/importers/adapters/pdf.py src/importers/adapters/epub.py \
        src/importers/adapters/docx.py requirements.txt \
        tests/test_importers/test_adapters.py
git commit -m "feat: add PDFAdapter, EpubAdapter, DocxAdapter; add openpyxl/python-docx/ebooklib deps"
```

---

## Task 8: Data adapters (Spreadsheet, JSON, Web, Image)

**Files:**
- Create: `src/importers/adapters/spreadsheet.py`
- Create: `src/importers/adapters/json_adapter.py`
- Create: `src/importers/adapters/web.py`
- Create: `src/importers/adapters/image.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_importers/test_adapters.py`:

```python
def test_spreadsheet_can_handle_csv(tmp_path):
    from src.importers.adapters.spreadsheet import SpreadsheetAdapter
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25")
    assert SpreadsheetAdapter().can_handle(f) is True


def test_spreadsheet_extracts_csv_rows(tmp_path):
    from src.importers.adapters.spreadsheet import SpreadsheetAdapter
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25")
    chunks = SpreadsheetAdapter().extract(f)
    assert len(chunks) == 2
    assert "Alice" in chunks[0].text
    assert "age: 30" in chunks[0].text


def test_json_adapter_can_handle(tmp_path):
    from src.importers.adapters.json_adapter import JSONAdapter
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    assert JSONAdapter().can_handle(f) is True


def test_json_adapter_extracts_text(tmp_path):
    from src.importers.adapters.json_adapter import JSONAdapter
    f = tmp_path / "data.json"
    f.write_text('{"name": "Alice", "role": "engineer"}')
    chunks = JSONAdapter().extract(f)
    assert len(chunks) >= 1
    assert "Alice" in chunks[0].text


def test_web_adapter_can_handle_url():
    from src.importers.adapters.web import WebAdapter
    assert WebAdapter().can_handle(Path("https://example.com")) is True
    assert WebAdapter().can_handle(Path("http://example.com")) is True
    assert WebAdapter().can_handle(Path("/local/file.md")) is False


def test_image_can_handle(tmp_path):
    from src.importers.adapters.image import ImageAdapter
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    assert ImageAdapter().can_handle(f) is True


def test_image_skips_gracefully_without_vision_model(tmp_path):
    from src.importers.adapters.image import ImageAdapter
    f = tmp_path / "photo.png"
    f.write_bytes(b"\x89PNG")
    # Should return empty list (no vision model loaded), not raise
    chunks = ImageAdapter().extract(f)
    assert isinstance(chunks, list)
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_adapters.py -k "spreadsheet or json_adapter or web or image" -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement `src/importers/adapters/spreadsheet.py`**

```python
# src/importers/adapters/spreadsheet.py
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
        chunks = []
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
```

- [ ] **Step 4: Implement `src/importers/adapters/json_adapter.py`**

```python
# src/importers/adapters/json_adapter.py
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
```

- [ ] **Step 5: Implement `src/importers/adapters/web.py`**

```python
# src/importers/adapters/web.py
from __future__ import annotations

from pathlib import Path

from ..base import BaseAdapter, Chunk


class WebAdapter(BaseAdapter):
    source_name = "web"

    def can_handle(self, path: Path) -> bool:
        s = str(path)
        return s.startswith("http://") or s.startswith("https://")

    def extract(self, path: Path) -> list[Chunk]:
        url = str(path)
        try:
            import trafilatura  # noqa: PLC0415
            downloaded = trafilatura.fetch_url(url)
            text = trafilatura.extract(downloaded) or ""
        except Exception:
            return []
        if not text.strip():
            return []
        return [Chunk(
            text=text.strip(),
            source="web",
            title=url,
            created_at="",
            metadata={"url": url},
        )]
```

- [ ] **Step 6: Implement `src/importers/adapters/image.py`**

```python
# src/importers/adapters/image.py
from __future__ import annotations

import logging
from pathlib import Path

from ..base import BaseAdapter, Chunk

logger = logging.getLogger(__name__)
_SUPPORTED = {".png", ".jpg", ".jpeg", ".webp"}


class ImageAdapter(BaseAdapter):
    source_name = "image"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _SUPPORTED

    def extract(self, path: Path) -> list[Chunk]:
        """OCR via mlx-vlm. Returns empty list if no vision model is loaded."""
        try:
            import httpx  # noqa: PLC0415
            import base64

            with open(path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            suffix = path.suffix.lower().lstrip(".")
            mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"

            resp = httpx.post(
                "http://localhost:8000/v1/chat/completions",
                json={
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image in detail, including any text visible."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                        ],
                    }],
                    "stream": False,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            if not text.strip():
                return []
            return [Chunk(
                text=text.strip(),
                source="image",
                title=path.name,
                created_at="",
                metadata={"path": str(path)},
            )]
        except Exception as exc:
            logger.debug("ImageAdapter skipping %s: %s", path.name, exc)
            return []
```

- [ ] **Step 7: Run to verify all tests pass**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/importers/adapters/spreadsheet.py src/importers/adapters/json_adapter.py \
        src/importers/adapters/web.py src/importers/adapters/image.py \
        tests/test_importers/test_adapters.py
git commit -m "feat: add SpreadsheetAdapter, JSONAdapter, WebAdapter, ImageAdapter"
```

---

## Task 9: DirectoryAdapter

**Files:**
- Create: `src/importers/adapters/directory.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_importers/test_adapters.py`:

```python
def test_directory_adapter_delegates_to_markdown(tmp_path):
    from src.importers.adapters.directory import DirectoryAdapter
    from src.importers.adapters.markdown import MarkdownAdapter
    (tmp_path / "notes.md").write_text("# Hello\n\nSome content.")
    (tmp_path / "other.xyz").write_text("unknown format")
    adapter = DirectoryAdapter(adapters=[MarkdownAdapter()])
    assert adapter.can_handle(tmp_path) is True
    chunks = adapter.extract(tmp_path)
    assert any("Hello" in c.text or "content" in c.text for c in chunks)


def test_directory_adapter_skips_unknown_files(tmp_path):
    from src.importers.adapters.directory import DirectoryAdapter
    (tmp_path / "file.xyz").write_text("unknown")
    adapter = DirectoryAdapter(adapters=[])
    chunks = adapter.extract(tmp_path)
    assert chunks == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_importers/test_adapters.py::test_directory_adapter_delegates_to_markdown -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement `src/importers/adapters/directory.py`**

```python
# src/importers/adapters/directory.py
from __future__ import annotations

import logging
from pathlib import Path

from ..base import BaseAdapter, Chunk

logger = logging.getLogger(__name__)

# File patterns to always skip
_SKIP_PATTERNS = {".git", "__pycache__", ".DS_Store", "node_modules"}


class DirectoryAdapter(BaseAdapter):
    """Recursively walks a directory, delegating each file to the right adapter."""

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
```

- [ ] **Step 4: Run to verify tests pass**

```bash
python -m pytest tests/test_importers/test_adapters.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/importers/adapters/directory.py tests/test_importers/test_adapters.py
git commit -m "feat: add DirectoryAdapter for recursive folder ingestion"
```

---

## Task 10: Wire ImportService with all adapters + CLI

**Files:**
- Modify: `src/importers/service.py` — add `build_default_service()` factory
- Create: `src/importers/cli.py`
- Modify: `Makefile`

- [ ] **Step 1: Write failing test for CLI**

Add to `tests/test_importers/test_service.py`:

```python
def test_build_default_service_has_all_adapters():
    from unittest.mock import MagicMock
    from src.importers.service import build_default_service
    plugin = MagicMock()
    plugin.collection = MagicMock()
    plugin.palace_path = "/fake/palace"
    plugin._available = True
    svc = build_default_service(plugin)
    names = {a.source_name for a in svc._adapters}
    assert "anthropic" in names
    assert "openai" in names
    assert "markdown" in names
    assert "pdf" in names
    assert "directory" in names
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_importers/test_service.py::test_build_default_service_has_all_adapters -v
```
Expected: `ImportError: cannot import name 'build_default_service'`

- [ ] **Step 3: Add `build_default_service` to `src/importers/service.py`**

Add this function at the bottom of `service.py`:

```python
def build_default_service(memory_plugin: "MemPalaceMemoryPlugin") -> ImportService:
    """Create an ImportService with all built-in adapters registered."""
    from .adapters.anthropic import AnthropicAdapter
    from .adapters.openai import OpenAIAdapter
    from .adapters.markdown import MarkdownAdapter
    from .adapters.pdf import PDFAdapter
    from .adapters.epub import EpubAdapter
    from .adapters.image import ImageAdapter
    from .adapters.spreadsheet import SpreadsheetAdapter
    from .adapters.json_adapter import JSONAdapter
    from .adapters.docx import DocxAdapter
    from .adapters.web import WebAdapter
    from .adapters.directory import DirectoryAdapter

    # Order matters: specific formats before generic directory walker
    leaf_adapters: list[BaseAdapter] = [
        AnthropicAdapter(),
        OpenAIAdapter(),
        MarkdownAdapter(),
        PDFAdapter(),
        EpubAdapter(),
        ImageAdapter(),
        SpreadsheetAdapter(),
        JSONAdapter(),
        DocxAdapter(),
        WebAdapter(),
    ]
    svc = ImportService(memory_plugin=memory_plugin)
    for adapter in leaf_adapters:
        svc.register(adapter)
    # DirectoryAdapter must be last — it delegates to all leaf adapters
    svc.register(DirectoryAdapter(adapters=list(leaf_adapters)))
    return svc
```

- [ ] **Step 4: Implement `src/importers/cli.py`**

```python
# src/importers/cli.py
"""
CLI entry point for knowledge import.

Usage:
    python -m src.importers.cli <path>
    make import path=<path>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "[" + "░" * width + "]"
    filled = int(width * current / total)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


async def _run(path: Path) -> int:
    """Return exit code: 0 = success, 1 = error."""
    # Import here to avoid circular imports at module level
    from ..plugins.mempalace_plugin import MemPalaceMemoryPlugin
    from .service import build_default_service

    plugin = MemPalaceMemoryPlugin()
    if not plugin._available:
        print("✗ MemPalace is not available. Run: pip install mempalace 'chromadb>=1.5.4'",
              file=sys.stderr)
        return 1

    svc = build_default_service(plugin)

    async for event in svc.run(path):
        status = event.get("status")
        if status == "detecting":
            print(f"▶ Detecting format for: {path.name}")
        elif status == "extracting":
            print(f"  Adapter: {event['adapter']}  ({event['total']} chunks)")
        elif status == "progress":
            bar = _progress_bar(event["current"], event["total"])
            print(f"\r  Extracting... {bar} {event['current']}/{event['total']}", end="", flush=True)
        elif status == "done":
            print(f"\n✓ Done — {event['stored']} stored, {event['skipped']} skipped (duplicates)")
            return 0
        elif status == "error":
            print(f"\n✗ Error: {event['message']}", file=sys.stderr)
            return 1
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.importers.cli <path>", file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.exists():
        print(f"✗ Path not found: {path}", file=sys.stderr)
        sys.exit(1)
    exit_code = asyncio.run(_run(path))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add `import` target to `Makefile`**

Add after the `e2e` target:

```makefile
# ── Knowledge import ──────────────────────────────────────────────────────────

import:
	@[ "$(path)" ] || (echo "Usage: make import path=<path-to-import>"; exit 1)
	$(PYTHON) -m src.importers.cli $(path)
```

- [ ] **Step 6: Run all importer tests**

```bash
python -m pytest tests/test_importers/ -v
```
Expected: all pass

- [ ] **Step 7: Smoke test the CLI with your actual export**

```bash
make import path=/Users/moatasem/Documents/desk/notes/Anthropic-export-data-2026-04-06-12-07-38-batch-0000
```
Expected output similar to:
```
▶ Detecting format for: Anthropic-export-data-...
  Adapter: anthropic  (N chunks)
  Extracting... [████████████████████] N/N
✓ Done — X stored, Y skipped (duplicates)
```

- [ ] **Step 8: Commit**

```bash
git add src/importers/service.py src/importers/cli.py Makefile \
        tests/test_importers/test_service.py
git commit -m "feat: add build_default_service factory and make import CLI command"
```

---

## Task 11: API endpoints

**Files:**
- Modify: `src/proxy.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_proxy.py` (existing file, find the imports section and add):

```python
def test_import_history_returns_list(client):
    resp = client.get("/api/import/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "imports" in data
    assert isinstance(data["imports"], list)


def test_import_rejects_missing_path(client):
    resp = client.post("/api/import", json={})
    assert resp.status_code == 422
```

Check how `client` is defined in `tests/test_proxy.py`:

```bash
grep -n "client\|TestClient\|fixture" tests/test_proxy.py | head -10
```

Use the same fixture pattern already in that file.

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_proxy.py::test_import_history_returns_list -v
```
Expected: `404 Not Found`

- [ ] **Step 3: Add endpoints to `src/proxy.py`**

Find the existing `@app.get("/api/memories")` block and add after the memory endpoints section:

```python
# ---------------------------------------------------------------------------
# Knowledge import
# ---------------------------------------------------------------------------

@app.post("/api/import")
async def api_import(request: Request) -> StreamingResponse:
    """Stream import progress as SSE events."""
    assert _plugin_manager is not None
    body = await request.json()
    path_str: str | None = body.get("path")
    if not path_str:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse({"error": "path is required"}, status_code=422)

    from .importers.service import build_default_service
    svc = build_default_service(_plugin_manager.memory_plugin)

    async def _stream() -> AsyncIterator[bytes]:
        import json as _json
        async for event in svc.run(Path(path_str)):
            yield f"data: {_json.dumps(event)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/api/import/history")
async def api_import_history() -> JSONResponse:
    from .store import list_import_history
    return JSONResponse({"imports": list_import_history()})
```

Also add `from pathlib import Path` to the proxy imports if not already present:

```bash
grep -n "^from pathlib\|^import pathlib" src/proxy.py | head -3
```

If missing, add `from pathlib import Path` to the imports section.

- [ ] **Step 4: Run to verify tests pass**

```bash
python -m pytest tests/test_proxy.py -v -k "import"
```
Expected: pass

- [ ] **Step 5: Run full test suite**

```bash
make test
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/proxy.py tests/test_proxy.py
git commit -m "feat: add POST /api/import (SSE) and GET /api/import/history endpoints"
```

---

## Task 12: HTML UI — Import section in settings panel

**Files:**
- Modify: `src/static/index.html`

The settings panel is in `index.html`. Search for the existing settings/preferences section to understand the pattern, then add the import section.

- [ ] **Step 1: Find the settings panel anchor**

```bash
grep -n "settings\|pref-panel\|id=\"pref\|Preferences\|System Prompt" src/static/index.html | head -20
```

- [ ] **Step 2: Add import section HTML**

Find the closing `</div>` of the preferences/settings panel and add before it:

```html
      <!-- Import Knowledge -->
      <div class="pref-section">
        <div class="pref-section-title">Import Knowledge</div>
        <div class="pref-row" style="flex-direction:column;align-items:flex-start;gap:8px;">
          <div style="display:flex;gap:8px;width:100%;">
            <input id="import-path-input" type="text" placeholder="Path to file, folder, or URL…"
              style="flex:1;font-size:12px;padding:6px 10px;border:1px solid var(--border);
                     border-radius:6px;background:var(--bg-2);color:var(--text);" />
            <button onclick="browseImportPath()" style="font-size:12px;padding:6px 12px;
              border:1px solid var(--border);border-radius:6px;background:var(--bg-2);
              color:var(--text);cursor:pointer;">Browse…</button>
            <button onclick="startImport()" id="import-btn" style="font-size:12px;padding:6px 14px;
              border-radius:6px;background:var(--accent);color:#fff;border:none;cursor:pointer;">
              Import
            </button>
          </div>
          <div id="import-progress-wrap" style="display:none;width:100%;">
            <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden;">
              <div id="import-progress-bar" style="height:100%;background:var(--accent);
                   width:0%;transition:width 0.2s;"></div>
            </div>
            <div id="import-status" style="font-size:11px;color:var(--text-dim);margin-top:4px;"></div>
          </div>
          <div id="import-history-wrap" style="width:100%;font-size:11px;color:var(--text-dim);">
            <div id="import-history-list"></div>
          </div>
        </div>
      </div>
```

- [ ] **Step 3: Add import JavaScript**

Find the end of the settings JS section (near other pref functions) and add:

```javascript
// ── Knowledge Import ──────────────────────────────────────────────────────────

function browseImportPath() {
  // HTML file inputs can't return full paths for security reasons.
  // The user must type or paste a path. This button focuses the input.
  document.getElementById('import-path-input').focus();
}

async function startImport() {
  const path = document.getElementById('import-path-input').value.trim();
  if (!path) return;
  const btn = document.getElementById('import-btn');
  const wrap = document.getElementById('import-progress-wrap');
  const bar = document.getElementById('import-progress-bar');
  const status = document.getElementById('import-status');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  wrap.style.display = 'block';
  bar.style.width = '0%';
  status.textContent = 'Starting…';
  try {
    const resp = await fetch('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }
        if (evt.status === 'extracting') {
          status.textContent = `Detected: ${evt.adapter} (${evt.total} chunks)`;
        } else if (evt.status === 'progress') {
          const pct = evt.total ? Math.round(100 * evt.current / evt.total) : 0;
          bar.style.width = pct + '%';
          status.textContent = `${evt.current}/${evt.total} chunks — ${evt.skipped} duplicates skipped`;
        } else if (evt.status === 'done') {
          bar.style.width = '100%';
          status.textContent = `✓ ${evt.stored} stored, ${evt.skipped} skipped`;
          loadImportHistory();
        } else if (evt.status === 'error') {
          status.textContent = `✗ ${evt.message}`;
        }
      }
    }
  } catch (e) {
    status.textContent = `✗ ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import';
  }
}

async function loadImportHistory() {
  try {
    const { imports = [] } = await (await fetch('/api/import/history')).json();
    const list = document.getElementById('import-history-list');
    if (!imports.length) { list.innerHTML = ''; return; }
    list.innerHTML = '<div style="margin-top:6px;font-weight:500;">Past imports:</div>' +
      imports.map(r =>
        `<div style="margin-top:2px;">${r.source} — ${r.stored} chunks — ${new Date(r.imported_at).toLocaleDateString()} — <span style="opacity:0.6">${r.path}</span></div>`
      ).join('');
  } catch (_) {}
}

// Load import history when preferences panel opens
```

Find where the preferences panel is shown/opened and add `loadImportHistory()` to that call. Search for:

```bash
grep -n "openPrefPanel\|showPref\|pref.*open\|loadPref" src/static/index.html | head -10
```

Add `loadImportHistory()` inside whichever function opens the preferences panel.

- [ ] **Step 4: Verify HTML is valid**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/static/index.html').read())
print('HTML parses without error')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/static/index.html
git commit -m "feat: add Import Knowledge section to HTML settings panel"
```

---

## Task 13: Swift UI — Import Knowledge in Settings

**Files:**
- Modify: `Loca-SwiftUI/Sources/Loca/Backend/Models.swift`
- Modify: `Loca-SwiftUI/Sources/Loca/Backend/BackendClient.swift`
- Modify: `Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift`

- [ ] **Step 1: Add `ImportHistoryItem` to `Models.swift`**

Find the `// MARK: - Plugins` section in `Models.swift` and add before it:

```swift
// MARK: - Import

struct ImportHistoryItem: Decodable, Identifiable {
    let source: String
    let path: String
    let stored: Int
    let skipped: Int
    let imported_at: String

    var id: String { "\(source)-\(imported_at)" }

    var importedDate: String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: imported_at) {
            return date.formatted(date: .abbreviated, time: .omitted)
        }
        return imported_at
    }
}

struct ImportHistoryResponse: Decodable {
    let imports: [ImportHistoryItem]
}

struct ImportProgressEvent: Decodable {
    let status: String
    let adapter: String?
    let total: Int?
    let current: Int?
    let stored: Int?
    let skipped: Int?
    let message: String?
}
```

- [ ] **Step 2: Add API methods to `BackendClient.swift`**

Find the existing `listMemories()` function in `BackendClient.swift` to understand the pattern, then add:

```swift
func fetchImportHistory() async throws -> [ImportHistoryItem] {
    let url = URL(string: "\(baseURL)/api/import/history")!
    let (data, _) = try await URLSession.shared.data(from: url)
    return try JSONDecoder().decode(ImportHistoryResponse.self, from: data).imports
}
```

- [ ] **Step 3: Add Import Knowledge section to `SettingsView.swift`**

Find the closing brace of the last `AckSection`-equivalent section in the settings scroll view and add a new section. First locate the right spot:

```bash
grep -n "Section\|VStack\|preferences\|SystemPrompt\|Inference" \
  Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift | head -30
```

Add a new `ImportSection` view at the bottom of `SettingsView.swift` (before the final closing brace):

```swift
// MARK: - Import Knowledge section

private struct ImportSection: View {
    @State private var importPath = ""
    @State private var isImporting = false
    @State private var progressPct: Double = 0
    @State private var statusText = ""
    @State private var history: [ImportHistoryItem] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("IMPORT KNOWLEDGE")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
                .tracking(0.8)

            HStack(spacing: 8) {
                TextField("Path to file, folder, or URL…", text: $importPath)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12))

                Button("File…") {
                    let panel = NSOpenPanel()
                    panel.canChooseFiles = true
                    panel.canChooseDirectories = false
                    panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK {
                        importPath = panel.url?.path ?? ""
                    }
                }
                .controlSize(.small)

                Button("Folder…") {
                    let panel = NSOpenPanel()
                    panel.canChooseFiles = false
                    panel.canChooseDirectories = true
                    panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK {
                        importPath = panel.url?.path ?? ""
                    }
                }
                .controlSize(.small)

                Button(isImporting ? "Importing…" : "Import") {
                    Task { await runImport() }
                }
                .controlSize(.small)
                .buttonStyle(.borderedProminent)
                .disabled(importPath.isEmpty || isImporting)
            }

            if isImporting || !statusText.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ProgressView(value: progressPct)
                        .progressViewStyle(.linear)
                    Text(statusText)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
            }

            if !history.isEmpty {
                Divider()
                Text("Past imports")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.secondary)
                ForEach(history) { item in
                    HStack {
                        Text(item.source)
                            .font(.system(size: 11, weight: .medium))
                        Text("—")
                        Text("\(item.stored) chunks")
                            .font(.system(size: 11))
                        Spacer()
                        Text(item.importedDate)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
            }
        }
        .task { await loadHistory() }
    }

    private func loadHistory() async {
        history = (try? await BackendClient.shared.fetchImportHistory()) ?? []
    }

    private func runImport() async {
        guard !importPath.isEmpty else { return }
        isImporting = true
        progressPct = 0
        statusText = "Starting…"

        guard let url = URL(string: "http://localhost:8000/api/import") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["path": importPath])

        do {
            let (bytes, _) = try await URLSession.shared.bytes(for: req)
            var buffer = ""
            for try await byte in bytes {
                let char = String(bytes: [byte], encoding: .utf8) ?? ""
                buffer += char
                if buffer.hasSuffix("\n\n") {
                    let lines = buffer.components(separatedBy: "\n")
                    for line in lines where line.hasPrefix("data: ") {
                        let payload = String(line.dropFirst(6))
                        if payload == "[DONE]" { break }
                        if let data = payload.data(using: .utf8),
                           let evt = try? JSONDecoder().decode(ImportProgressEvent.self, from: data) {
                            await MainActor.run { handleEvent(evt) }
                        }
                    }
                    buffer = ""
                }
            }
        } catch {
            statusText = "✗ \(error.localizedDescription)"
        }

        isImporting = false
        await loadHistory()
    }

    @MainActor
    private func handleEvent(_ evt: ImportProgressEvent) {
        switch evt.status {
        case "extracting":
            statusText = "Detected: \(evt.adapter ?? "") (\(evt.total ?? 0) chunks)"
        case "progress":
            let current = Double(evt.current ?? 0)
            let total = Double(evt.total ?? 1)
            progressPct = total > 0 ? current / total : 0
            statusText = "\(evt.current ?? 0)/\(evt.total ?? 0) — \(evt.skipped ?? 0) duplicates skipped"
        case "done":
            progressPct = 1.0
            statusText = "✓ \(evt.stored ?? 0) stored, \(evt.skipped ?? 0) skipped"
        case "error":
            statusText = "✗ \(evt.message ?? "Unknown error")"
        default:
            break
        }
    }
}
```

Now add `ImportSection()` inside the settings scroll view. Find where other sections are added and add it:

```bash
grep -n "AckSection\|Section(header\|inferenceSection\|VaultSection" \
  Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift | tail -10
```

Add `ImportSection()` after the last existing section in the settings panel scroll view.

- [ ] **Step 4: Build Swift**

```bash
swift build --package-path Loca-SwiftUI 2>&1 | tail -5
```
Expected: `Build complete!`

- [ ] **Step 5: Commit**

```bash
git add Loca-SwiftUI/Sources/Loca/Backend/Models.swift \
        Loca-SwiftUI/Sources/Loca/Backend/BackendClient.swift \
        Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift
git commit -m "feat: add Import Knowledge section to Swift settings panel"
```

---

## Task 14: Full check + build + push

- [ ] **Step 1: Run all checks**

```bash
make all
```
Expected: `✓ all checks passed`

- [ ] **Step 2: Build and install app**

```bash
./build_app.sh
```
Expected: `Done — open Loca.app to launch.`

- [ ] **Step 3: Smoke test the full flow**

1. Open Loca → Settings → scroll to "Import Knowledge"
2. Click "Folder…" → select the Anthropic export folder
3. Click "Import"
4. Watch the progress bar fill
5. See completion summary
6. Open Memories panel — verify new chunks are searchable

- [ ] **Step 4: Push**

```bash
git push origin fix/quick-wins
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `src/importers/` package + `base.py` | Task 1 |
| `import_history` SQLite table | Task 2 |
| MemPalace plugin exposes collection | Task 3 |
| `ImportService` — dedup, chunking, progress, storage | Task 4 |
| `AnthropicAdapter` — conversations, memories, projects | Task 5 |
| `MarkdownAdapter`, `OpenAIAdapter` | Task 6 |
| `PDFAdapter`, `EpubAdapter`, `DocxAdapter` | Task 7 |
| `SpreadsheetAdapter`, `JSONAdapter`, `WebAdapter`, `ImageAdapter` | Task 8 |
| `DirectoryAdapter` | Task 9 |
| `build_default_service()` factory + CLI + `make import` | Task 10 |
| `POST /api/import` (SSE), `GET /api/import/history` | Task 11 |
| HTML settings panel — import section | Task 12 |
| Swift settings panel — import section | Task 13 |
| New deps: openpyxl, python-docx, ebooklib | Task 7 |
| Test fixtures | Task 5 |

All requirements covered. No placeholders. Types are consistent throughout (Chunk, ImportResult, BaseAdapter used identically in all tasks).
