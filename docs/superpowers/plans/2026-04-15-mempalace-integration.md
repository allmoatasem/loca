# MemPalace Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Loca's broken LLM-extraction memory system with MemPalace — verbatim storage + semantic search over past conversations.

**Architecture:** `MemPalaceMemoryPlugin` implements the existing `MemoryPlugin` abstract interface and is wired into `plugin_manager.py` via a new `type: mempalace` config value. The orchestrator gains a `_build_recall_query()` helper that uses the last 3 messages as the search query instead of just the last user message. The three-pass LLM extractor (`memory_extractor.py`) is deleted entirely.

**Tech Stack:** Python, MemPalace (PyPI), ChromaDB ≥1.5.4, existing plugin framework (`MemoryPlugin` ABC, `PluginManager`).

**Branch:** `feat/mempalace-memory`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Add | `src/plugins/mempalace_plugin.py` | `MemPalaceMemoryPlugin` — store, recall, list, delete, update via MemPalace Python API |
| Modify | `src/plugin_manager.py` | Add `mempalace` type branch; wire `MemPalaceMemoryPlugin` |
| Modify | `config.yaml` | Switch `plugins.memory.type` to `mempalace`; remove stale comments |
| Modify | `src/orchestrator.py` | Use last 3 messages for recall query; store user+assistant pairs; remove LLM extraction legacy path and import |
| Modify | `src/proxy.py` | `GET /api/memories` reads from plugin, not SQLite |
| Modify | `requirements.txt` | Add `mempalace`, `chromadb>=1.5.4` |
| Delete | `src/memory_extractor.py` | Replaced by MemPalace |
| Delete | `tests/test_memory_extractor.py` | No longer relevant |
| Add | `tests/test_mempalace_plugin.py` | Unit tests with mocked MemPalace |

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add packages to requirements.txt**

Open `requirements.txt` and append these two lines at the end:

```
mempalace>=0.1.0
chromadb>=1.5.4
```

- [ ] **Step 2: Install**

```bash
cd /Users/moatasem/software/playground/llm/Loca
pip install mempalace "chromadb>=1.5.4"
```

Expected: both install without error. On ARM64 Mac, ChromaDB ≥1.5.4 is required to avoid the old segfault — verify the installed version:

```bash
pip show chromadb | grep Version
```

Expected output contains `Version: 1.5.` or higher.

- [ ] **Step 3: Verify MemPalace imports**

```bash
python -c "from mempalace.searcher import get_collection, search_memories; from mempalace.miner import add_drawer; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add mempalace and chromadb>=1.5.4 dependencies"
```

---

## Task 2: Create MemPalaceMemoryPlugin with tests

**Files:**
- Create: `src/plugins/mempalace_plugin.py`
- Create: `tests/test_mempalace_plugin.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_mempalace_plugin.py`:

```python
"""Unit tests for MemPalaceMemoryPlugin."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to fake MemPalace imports
# ---------------------------------------------------------------------------

def _make_fake_mempalace(collection: MagicMock) -> None:
    """Inject fake mempalace modules into sys.modules."""
    fake_searcher = MagicMock()
    fake_searcher.get_collection.return_value = collection
    fake_searcher.search_memories.return_value = {
        "results": [
            {
                "id": "abc123",
                "content": "User prefers Python over JavaScript.",
                "room": "preferences",
                "timestamp": "2026-04-15T10:00:00",
                "distance": 0.1,
            }
        ]
    }

    fake_miner = MagicMock()
    fake_miner.add_drawer.return_value = None

    fake_mempalace = MagicMock()

    sys.modules.setdefault("mempalace", fake_mempalace)
    sys.modules["mempalace.searcher"] = fake_searcher
    sys.modules["mempalace.miner"] = fake_miner


def _remove_fake_mempalace() -> None:
    for key in list(sys.modules.keys()):
        if key.startswith("mempalace"):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMemPalaceMemoryPlugin:
    def setup_method(self):
        self.collection = MagicMock()
        _make_fake_mempalace(self.collection)

    def teardown_method(self):
        _remove_fake_mempalace()

    def _make_plugin(self):
        # Import after fakes are in sys.modules
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        with patch("src.plugins.mempalace_plugin._PALACE_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.__str__ = lambda s: "/fake/palace"
            plugin = MemPalaceMemoryPlugin()
        return plugin

    def test_init_success(self):
        plugin = self._make_plugin()
        assert plugin._available is True

    def test_init_failure_disables_plugin(self):
        _remove_fake_mempalace()  # break import
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin()
        assert plugin._available is False

    @pytest.mark.asyncio
    async def test_store_returns_id(self):
        plugin = self._make_plugin()
        mid = await plugin.store("I prefer Python.", {})
        assert isinstance(mid, str)
        assert len(mid) > 0

    @pytest.mark.asyncio
    async def test_store_when_unavailable_returns_empty(self):
        _remove_fake_mempalace()
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin()
        mid = await plugin.store("anything", {})
        assert mid == ""

    @pytest.mark.asyncio
    async def test_recall_returns_formatted_results(self):
        plugin = self._make_plugin()
        results = await plugin.recall("Python preference", limit=5)
        assert len(results) == 1
        assert results[0]["content"] == "User prefers Python over JavaScript."
        assert results[0]["type"] == "preferences"

    @pytest.mark.asyncio
    async def test_recall_empty_query_returns_empty(self):
        plugin = self._make_plugin()
        results = await plugin.recall("", limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_when_unavailable_returns_empty(self):
        _remove_fake_mempalace()
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin()
        results = await plugin.recall("anything", limit=5)
        assert results == []

    def test_list_all_returns_memories(self):
        plugin = self._make_plugin()
        mems = plugin.list_all()
        assert isinstance(mems, list)

    def test_list_all_with_type_filter(self):
        plugin = self._make_plugin()
        # collection.get returns empty when filtered
        self.collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mems = plugin.list_all(type="decisions")
        assert isinstance(mems, list)

    def test_delete_calls_collection(self):
        plugin = self._make_plugin()
        plugin.delete("abc123")
        self.collection.delete.assert_called_once_with(ids=["abc123"])

    def test_delete_when_unavailable_does_nothing(self):
        _remove_fake_mempalace()
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin()
        plugin.delete("abc123")  # must not raise

    def test_update_calls_collection(self):
        plugin = self._make_plugin()
        plugin.update("abc123", "updated content")
        self.collection.update.assert_called_once_with(
            ids=["abc123"], documents=["updated content"]
        )

    def test_classify_room_decisions(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("We decided to use PostgreSQL because of reliability") == "decisions"

    def test_classify_room_preferences(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("I prefer Python over JavaScript") == "preferences"

    def test_classify_room_problems(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("There was a crash in the download module") == "problems"

    def test_classify_room_general_fallback(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("The weather is nice today") == "general"
```

- [ ] **Step 2: Run tests — expect ImportError (plugin doesn't exist yet)**

```bash
cd /Users/moatasem/software/playground/llm/Loca
python -m pytest tests/test_mempalace_plugin.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `ImportError` for `mempalace_plugin`.

- [ ] **Step 3: Create `src/plugins/mempalace_plugin.py`**

```python
"""
MemPalace-backed memory plugin for Loca.

Stores conversation exchanges verbatim in a local MemPalace palace
(~/.mempalace/palace) and retrieves relevant memories via semantic search.

Falls back gracefully if MemPalace or ChromaDB is unavailable — Loca keeps
working, just without memory recall.
"""
from __future__ import annotations

import hashlib
import logging
import time
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
        except Exception as exc:
            logger.warning(
                "MemPalace unavailable (%s) — memory disabled. "
                "Install with: pip install mempalace 'chromadb>=1.5.4'",
                exc,
            )

    # ------------------------------------------------------------------
    # MemoryPlugin interface
    # ------------------------------------------------------------------

    async def store(self, text: str, metadata: dict) -> str:
        if not self._available or not text.strip():
            return ""
        try:
            from mempalace.miner import add_drawer  # noqa: PLC0415
            source = f"loca-chat-{int(time.time())}"
            chunk_index = 0
            add_drawer(
                self._collection,
                wing="loca",
                room=_classify_room(text),
                content=text,
                source_file=source,
                chunk_index=chunk_index,
                agent="loca",
            )
            return hashlib.sha256(f"{source}{chunk_index}".encode()).hexdigest()[:16]
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
                    "id": r["id"],
                    "content": r["content"],
                    "type": r.get("room", "general"),
                    "created": r.get("timestamp", ""),
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
                    "content": (result["documents"] or [""])[i],
                    "type": ((result["metadatas"] or [{}])[i]).get("room", "general"),
                    "created": ((result["metadatas"] or [{}])[i]).get("timestamp", ""),
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
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/test_mempalace_plugin.py -v
```

Expected: all tests pass. If any fail, fix the implementation before continuing.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -v --ignore=tests/e2e -x
```

Expected: all passing (same as before this task).

- [ ] **Step 6: Commit**

```bash
git add src/plugins/mempalace_plugin.py tests/test_mempalace_plugin.py
git commit -m "feat: add MemPalaceMemoryPlugin with tests"
```

---

## Task 3: Wire MemPalaceMemoryPlugin into plugin_manager + config

**Files:**
- Modify: `src/plugin_manager.py:53-68`
- Modify: `config.yaml:83-91`

- [ ] **Step 1: Update `plugin_manager.py` to support `type: mempalace`**

Find the `start()` method (lines 53–68). Replace the entire method body:

```python
async def start(self) -> None:
    """Instantiate / start all configured plugins."""
    mem_cfg = self._config.get("memory", {})
    plugin_type = mem_cfg.get("type", "builtin")

    if plugin_type == "mempalace":
        from .plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin()
        if plugin._available:
            self._memory = plugin
            logger.info("Memory plugin: MemPalace (verbatim + semantic search)")
        else:
            self._memory = BuiltinMemoryPlugin(self._backend)
            logger.warning("MemPalace unavailable — fell back to built-in memory plugin")
    elif plugin_type == "external":
        await self._start_external("memory", mem_cfg)
        self._memory = BuiltinMemoryPlugin(self._backend)
        logger.warning("External plugin type not fully wired — using built-in")
    else:
        self._memory = BuiltinMemoryPlugin(self._backend)
        logger.info("Memory plugin: built-in (verbatim + semantic retrieval)")
```

Also update `status()` to handle the `mempalace` type (lines 93–115). Replace the description string inside the return dict:

```python
def status(self) -> dict:
    """Return plugin status for /api/plugins endpoint."""
    mem_cfg = self._config.get("memory", {})
    mem_type = mem_cfg.get("type", "builtin")
    mem_running = (
        self._procs["memory"].returncode is None
        if "memory" in self._procs
        else True  # built-in and mempalace are always "running" (in-process)
    )
    descriptions = {
        "builtin": "Verbatim storage + semantic retrieval via local embeddings",
        "mempalace": "MemPalace verbatim storage + semantic search (ChromaDB)",
        "external": f"External plugin on port {mem_cfg.get('port', '?')}",
    }
    return {
        "plugins": [
            {
                "name": "memory",
                "type": mem_type,
                "running": mem_running,
                "description": descriptions.get(mem_type, mem_type),
            }
        ]
    }
```

- [ ] **Step 2: Update `config.yaml` plugins section**

Replace the `plugins:` block at the bottom of `config.yaml`:

```yaml
# ── Plugins ──────────────────────────────────────────────────────────────────
# memory.type: builtin   — verbatim storage + semantic search via local embeddings
# memory.type: mempalace — MemPalace verbatim storage + ChromaDB semantic search
plugins:
  memory:
    type: mempalace
```

- [ ] **Step 3: Run the test suite**

```bash
python -m pytest tests/ -v --ignore=tests/e2e -x
```

Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add src/plugin_manager.py config.yaml
git commit -m "feat: wire MemPalaceMemoryPlugin into plugin_manager, set type: mempalace"
```

---

## Task 4: Update orchestrator — recall query + store strategy

**Files:**
- Modify: `src/orchestrator.py`

The orchestrator currently recalls using only `user_message`. We change it to use the last 3 messages (user+assistant pairs) for richer context. We also update `_store_verbatim` to store full conversation pairs instead of only user messages, then remove the legacy LLM-extraction fallback.

- [ ] **Step 1: Update the recall call in `handle()` (lines 94–101)**

Find this block:

```python
        # Memory injection: use semantic recall if plugin available, else legacy bulk inject
        if self._memory:
            relevant = await self._memory.recall(user_message, limit=6)
            mem_ctx = self._memory.format_for_prompt(relevant)
        else:
            mem_ctx = get_memories_context()
```

Replace with:

```python
        # Memory injection: recall using last 3 messages for conversational context
        if self._memory:
            recall_query = _build_recall_query(messages)
            relevant = await self._memory.recall(recall_query, limit=6)
            mem_ctx = self._memory.format_for_prompt(relevant)
        else:
            mem_ctx = get_memories_context()
```

- [ ] **Step 2: Add `_build_recall_query` helper at the bottom of the file (after `_extract_tool_call`)**

```python
def _build_recall_query(messages: list[dict]) -> str:
    """
    Build a recall query from the last 3 user+assistant messages.

    Using multiple turns gives MemPalace enough conversational thread to surface
    memories relevant to the current topic, not just the last keyword typed.
    """
    recent: list[str] = []
    for msg in messages[-6:]:  # last 6 entries covers ~3 pairs
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        recent.append(content.strip())
    return " ".join(recent)[-2000:]  # cap at 2000 chars to avoid huge queries
```

- [ ] **Step 3: Update `_store_verbatim` to store user+assistant pairs (lines 440–466)**

Replace the entire `_store_verbatim` method:

```python
async def _store_verbatim(
    self, messages: list[dict], conv_id: str | None
) -> list[dict]:
    """
    Store the last user+assistant exchange verbatim.

    Storing the pair gives MemPalace full context for room classification
    (decisions, preferences, problems, etc.) rather than just the user side.
    """
    assert self._memory is not None
    saved = []

    # Find the last user message and its following assistant reply
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return []

    user_content = messages[last_user_idx].get("content", "")
    if not isinstance(user_content, str) or len(user_content.strip()) < 20:
        return []

    # Look for the immediately following assistant message
    assistant_content = ""
    if last_user_idx + 1 < len(messages):
        next_msg = messages[last_user_idx + 1]
        if next_msg.get("role") == "assistant":
            assistant_content = next_msg.get("content", "")

    if assistant_content:
        text = f"User: {user_content.strip()}\n\nAssistant: {assistant_content.strip()}"
    else:
        text = user_content.strip()

    mid = await self._memory.store(text, {"conv_id": conv_id})
    if mid:
        saved.append({"id": mid, "content": text[:120] + ("..." if len(text) > 120 else ""), "type": "conversation"})
    return saved
```

- [ ] **Step 4: Remove the legacy LLM-extraction path from `extract_and_save_memories` (lines 418–438)**

Replace the entire `extract_and_save_memories` method:

```python
async def extract_and_save_memories(
    self, messages: list[dict], conv_id: str | None = None
) -> list[dict]:
    """Store the last conversation exchange verbatim via the memory plugin."""
    if self._memory:
        return await self._store_verbatim(messages, conv_id)
    return []
```

- [ ] **Step 5: Remove the `memory_extractor` import and unused `add_memory` import (lines 23–27)**

Find these imports at the top of `orchestrator.py`:

```python
from .memory_extractor import extract_memories
from .model_manager import ModelManager
from .plugins.memory_plugin import MemoryPlugin
from .router import Model, RouteResult, route
from .store import add_memory, get_memories_context
```

Replace with (removing `extract_memories` and `add_memory`):

```python
from .model_manager import ModelManager
from .plugins.memory_plugin import MemoryPlugin
from .router import Model, RouteResult, route
from .store import get_memories_context
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/e2e -x
```

Expected: all passing. `test_orchestrator.py` tests that reference `extract_and_save_memories` should still pass since the method exists (just simplified).

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator.py
git commit -m "feat: update orchestrator to use last-3-message recall and pair-based store"
```

---

## Task 5: Update proxy — memory endpoints use plugin, not SQLite

**Files:**
- Modify: `src/proxy.py`

Two endpoints still call SQLite directly: `GET /api/memories` (list) and `POST /api/memories` (manual add). Both must route through the memory plugin.

- [ ] **Step 1: Replace the GET /api/memories endpoint (around line 763)**

Find:

```python
@app.get("/api/memories")
async def api_list_memories(type: str | None = None) -> JSONResponse:
    return JSONResponse({"memories": list_memories(type=type)})
```

Replace with:

```python
@app.get("/api/memories")
async def api_list_memories(type: str | None = None) -> JSONResponse:
    assert _plugin_manager is not None
    memories = _plugin_manager.memory_plugin.list_all(type=type)
    return JSONResponse({"memories": memories})
```

- [ ] **Step 2: Replace the POST /api/memories endpoint (around line 768)**

Find:

```python
@app.post("/api/memories")
async def api_add_memory(request: Request) -> JSONResponse:
    body = await request.json()
    mid = add_memory(
        content=body.get("content", ""),
```

Replace the full endpoint with:

```python
@app.post("/api/memories")
async def api_add_memory(request: Request) -> JSONResponse:
    assert _plugin_manager is not None
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    mid = await _plugin_manager.memory_plugin.store(content, {})
    return JSONResponse({"id": mid})
```

- [ ] **Step 3: Clean up unused imports in proxy.py**

In the `from .store import (...)` block at the top of `proxy.py`, remove `add_memory` and `list_memories` if they are no longer used anywhere else in the file. Check first:

```bash
grep -n "add_memory\|list_memories" src/proxy.py
```

If only the lines you just replaced reference them, remove them from the import block. The remaining store imports (`delete_conversation`, `list_conversations`, `list_vault_notes`, etc.) stay.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/e2e -x
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/proxy.py
git commit -m "feat: memory API endpoints route through plugin instead of SQLite"
```

---

## Task 6: Delete memory_extractor and its tests

**Files:**
- Delete: `src/memory_extractor.py`
- Delete: `tests/test_memory_extractor.py`

- [ ] **Step 1: Confirm nothing else imports memory_extractor**

```bash
grep -r "memory_extractor" /Users/moatasem/software/playground/llm/Loca/src/
```

Expected: no output. If any file still imports it, fix that import first.

- [ ] **Step 2: Delete the files**

```bash
rm /Users/moatasem/software/playground/llm/Loca/src/memory_extractor.py
rm /Users/moatasem/software/playground/llm/Loca/tests/test_memory_extractor.py
```

- [ ] **Step 3: Run the full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/e2e -x
```

Expected: all passing. The deleted test file is gone; no remaining test imports it.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "feat: remove memory_extractor and its tests (replaced by MemPalace)"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run ruff + mypy**

```bash
cd /Users/moatasem/software/playground/llm/Loca
ruff check src/ tests/
mypy src/ --ignore-missing-imports
```

Expected: no errors.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/e2e
```

Expected: all passing.

- [ ] **Step 3: Manual smoke test**

Start Loca and have a short conversation. Then in a separate terminal:

```bash
mempalace search "user preference" --wing loca 2>/dev/null || \
python -c "
from mempalace.searcher import search_memories
import json
r = search_memories('user preference', palace_path='$HOME/.mempalace/palace', wing='loca', n_results=3)
print(json.dumps(r, indent=2))
"
```

Expected: the conversation you just had appears in the results.

- [ ] **Step 4: Test the fallback — temporarily break the import**

```bash
python -c "
import sys
sys.modules['mempalace'] = None  # simulate import failure
from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
p = MemPalaceMemoryPlugin()
print('available:', p._available)  # should print: available: False
"
```

Expected: `available: False` with a warning log. No crash.

- [ ] **Step 5: Commit and push**

```bash
git push -u origin feat/mempalace-memory
```

---

## Summary of commits on this branch

1. `feat: add mempalace and chromadb>=1.5.4 dependencies`
2. `feat: add MemPalaceMemoryPlugin with tests`
3. `feat: wire MemPalaceMemoryPlugin into plugin_manager, set type: mempalace`
4. `feat: update orchestrator to use last-3-message recall and pair-based store`
5. `feat: /api/memories list reads from memory plugin instead of SQLite`
6. `feat: remove memory_extractor and its tests (replaced by MemPalace)`
