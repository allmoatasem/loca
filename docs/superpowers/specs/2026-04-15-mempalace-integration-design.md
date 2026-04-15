# MemPalace Integration Design

**Date:** 2026-04-15  
**Status:** Approved  
**Branch:** feat/mempalace-memory

---

## Problem

Loca's current memory system is broken:

- Three-pass LLM extraction (`memory_extractor.py`) makes incorrect assumptions and often fails silently ("Extraction failed: The data couldn't be read because it is missing.")
- Raw conversation messages leak into the memory list instead of extracted facts
- Memory recall is unreliable — not always injected into the system prompt
- The approach is fundamentally lossy: an LLM decides what's worth remembering, and gets it wrong

---

## Goal

Replace the LLM-extraction memory system with MemPalace — a verbatim, semantically-searchable memory library. Start fresh (no migration of existing memories).

---

## Approach

**Direct Python library import.** MemPalace is installed as a Python dependency and called directly from `src/memory.py`. No subprocess worker, no HTTP server, no MCP protocol — just `from mempalace.layers import MemoryStack`.

The ARM64/ChromaDB segfault that previously blocked this is resolved in ChromaDB ≥1.5.4.

The plugin framework (`src/plugin_manager.py`, `src/plugins/memory_plugin.py`) is already in main and will be used as the integration point.

---

## Architecture

```
orchestrator.py
    └── plugin_manager.py (PluginManager)
            └── src/plugins/memory_plugin.py (MemPalaceMemoryPlugin)
                    └── MemPalace (pip dependency)
                            └── ~/.mempalace/palace/   ← local palace on disk
```

### `src/plugins/memory_plugin.py`

A new `MemPalaceMemoryPlugin` class implementing the existing `MemoryPlugin` abstract interface:

```python
class MemPalaceMemoryPlugin(MemoryPlugin):
    async def store(self, text: str, metadata: dict) -> str: ...
    async def recall(self, query: str, limit: int = 5) -> list[dict]: ...
    def list_all(self, type: str | None = None) -> list[dict]: ...
    def delete(self, mem_id: str) -> None: ...
    def update(self, mem_id: str, content: str) -> None: ...
```

On init:
- Checks if `~/.mempalace/palace/` exists; runs `mempalace init` if not
- Instantiates `MemoryStack(palace_path=..., wing="loca")`
- If MemPalace import fails, sets `_available = False` and degrades silently (falls back to `BuiltinMemoryPlugin`)

`config.yaml` switches `plugins.memory.type` from `builtin` to `mempalace`.

---

## Data Flow

### Before inference (recall)

1. `orchestrator.py` calls `plugin_manager.memory_plugin.recall(last_3_messages_as_text, limit=5)`
2. `MemPalaceMemoryPlugin` calls `stack.search(query, wing="loca", n_results=5)`
3. Results injected into system prompt as `<memory>` block
4. If unavailable or search fails: returns `[]`, no `<memory>` block — conversation continues normally

### After inference (store)

1. `orchestrator.py` calls `plugin_manager.memory_plugin.store(user_msg + "\n\n" + assistant_reply, metadata={})` — **non-blocking** (`asyncio.create_task`)
2. `MemPalaceMemoryPlugin` calls `tool_add_drawer(wing="loca", content=..., added_by="loca")`
3. MemPalace classifies the content into a room automatically (decisions, preferences, milestones, problems, emotional context)
4. Stored verbatim in ChromaDB under `~/.mempalace/palace/`

---

## Palace Structure

All memories stored under a single wing: `"loca"`.

MemPalace automatically assigns rooms based on content classification:

| Room | Example content |
|---|---|
| `decisions` | "We went with X because Y" |
| `preferences` | "Always use Python, not JS" |
| `milestones` | "Finally got the download resuming to work" |
| `problems` | "Crashed because ChromaDB ARM64 segfault" |
| `emotional` | "I feel overwhelmed by the complexity" |

---

## Recall Query

`recall()` receives the last 3 user+assistant messages concatenated as a single string. This gives MemPalace enough conversational thread to find memories relevant to what is being discussed, not just the last keyword.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| MemPalace not installed | Auto-installed via `pip install mempalace chromadb>=1.5.4` on startup |
| ChromaDB import error | Falls back to `BuiltinMemoryPlugin`, logs warning |
| `store()` fails | Logged and swallowed — never blocks inference |
| `recall()` fails | Returns `[]`, no memory block injected |
| Palace not initialised | `mempalace init` run automatically on first startup |

---

## What Changes

| Action | File | Notes |
|---|---|---|
| **Delete** | `src/memory_extractor.py` | Entire file removed |
| **Add** | `src/plugins/mempalace_plugin.py` | `MemPalaceMemoryPlugin` implementing `MemoryPlugin` |
| **Modify** | `src/plugin_manager.py` | Wire `MemPalaceMemoryPlugin` when `type: mempalace`; remove TODO stub |
| **Modify** | `src/orchestrator.py` | Swap `extract_and_save_memories()` for `store()`; add `recall()` before inference using last 3 messages |
| **Modify** | `src/store.py` | Remove extraction helpers |
| **Modify** | `src/proxy.py` | Memory API endpoints read from MemPalace via `recall("")` for listing |
| **Modify** | `config.yaml` | Set `plugins.memory.type: mempalace` |
| **Modify** | `requirements.txt` | Add `mempalace`, `chromadb>=1.5.4` |
| **Delete** | `tests/test_memory_extractor.py` | No longer relevant |
| **Add** | `tests/test_mempalace_plugin.py` | Unit tests with mocked `MemoryStack` |

---

## Memory UI Panel

The existing memory panel (`/api/memories`) currently reads from SQLite. With MemPalace, the source of truth moves to ChromaDB. The panel endpoint will be updated to call `recall(query="")` (empty query = return recent memories) so it reads from MemPalace instead. The SQLite `memories` table is kept as-is but no longer written to.

---

## Testing

- **Unit**: `tests/test_mempalace_plugin.py` — mock `MemoryStack`, test store/recall/fallback paths
- **Manual**: Start Loca, have a conversation, run `mempalace search "..."` in terminal to confirm storage
- **Fallback**: Temporarily break the import, confirm Loca still starts and inference works

---

## Out of Scope

- MCP server integration (planned separately)
- Migration of existing SQLite memories
- Per-user palace isolation (all memories in a single `loca` wing for now)
