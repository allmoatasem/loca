# Knowledge Import Pipeline — Design Spec

**Date:** 2026-04-16
**Status:** Approved
**Branch:** TBD (implement on fresh branch from main)

---

## Problem

Loca's knowledge base only contains what was said in its own chat sessions. Users have years of existing knowledge in other places: Claude/ChatGPT conversation exports, Obsidian vaults, PDFs, books, spreadsheets, markdown notes. There is no way to bring that knowledge into Loca so it can be recalled during inference.

---

## Goal

A provider-agnostic knowledge ingestion pipeline that accepts any supported source — AI provider exports, documents, files, URLs, directories — parses them into chunks, deduplicates, and stores verbatim in MemPalace. Available both as a CLI command (`make import path=...`) and through a Settings panel UI. Adding a new source format requires writing one new adapter class and nothing else.

---

## Architecture

```
src/importers/
    __init__.py
    base.py              — Chunk, ImportResult, BaseAdapter ABC
    service.py           — ImportService: registry, detection, dedup, chunking, storage, progress
    cli.py               — CLI entry point (python -m src.importers.cli)
    adapters/
        __init__.py
        anthropic.py     — Anthropic data export (conversations, memories, projects)
        openai.py        — ChatGPT data export
        markdown.py      — .md / .txt / .rst files and directories
        pdf.py           — PDF files (pypdf, already in deps)
        epub.py          — EPUB books
        image.py         — Images via OCR (mlx-vlm, already integrated)
        spreadsheet.py   — .csv / .xlsx
        json_adapter.py  — Generic JSON files
        docx.py          — .docx (python-docx, new dep)
        web.py           — URLs via trafilatura (already in deps)
        directory.py     — Any folder: walks recursively, delegates per file
```

All imported chunks land in MemPalace wing `"loca"` alongside live conversation memory. A `source` metadata field distinguishes imported content from live conversations. A single MemPalace semantic search query surfaces both.

---

## Core Types (`base.py`)

```python
@dataclass
class Chunk:
    text: str
    source: str          # adapter name: "anthropic", "markdown", "pdf", etc.
    title: str           # conversation name, filename, section heading
    created_at: str      # ISO timestamp if available from source, "" otherwise
    metadata: dict       # adapter-specific extras: conv_id, page, project name, etc.

@dataclass
class ImportResult:
    total: int           # chunks extracted by adapter
    stored: int          # chunks written to MemPalace (new)
    skipped: int         # duplicates skipped
    source: str          # adapter that handled this import

class BaseAdapter(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def extract(self, path: Path) -> list[Chunk]: ...
```

Format detection: `ImportService` iterates registered adapters in priority order and calls `can_handle()`. First match wins. `DirectoryAdapter` is always last — it handles anything that is a folder and delegates each file to the appropriate adapter.

---

## Adapter Registry

| Adapter | `can_handle()` trigger | Notes |
|---|---|---|
| `AnthropicAdapter` | directory containing `conversations.json` with `chat_messages` key | Conversations, memories doc, project docs |
| `OpenAIAdapter` | directory containing `conversations.json` with `mapping` key | ChatGPT export — same filename, different schema |
| `MarkdownAdapter` | `.md`, `.txt`, `.rst` file | Chunk by heading, fallback to fixed-size |
| `PDFAdapter` | `.pdf` file | pypdf (already in deps), chunk by page |
| `EpubAdapter` | `.epub` file | Chunk by chapter |
| `ImageAdapter` | `.png`, `.jpg`, `.jpeg`, `.webp` | OCR via mlx-vlm (already integrated) |
| `SpreadsheetAdapter` | `.csv`, `.xlsx` | Each row or sheet summary as a chunk |
| `JSONAdapter` | `.json` (not a known export format) | Flatten to readable text |
| `DocxAdapter` | `.docx` | python-docx (new dep) |
| `WebAdapter` | `http://` or `https://` path | trafilatura (already in deps) |
| `DirectoryAdapter` | any directory | Walks recursively, delegates per file, skips unknowns |

**Out of scope for this version:** audio files (STT via mlx-whisper — too ambitious, separate feature).

---

## Adapter Specifics

### AnthropicAdapter

Anthropic exports contain four files. Each is handled differently:

**conversations.json** — each conversation becomes one chunk per user+assistant exchange (same pattern as live MemPalace storage). Metadata includes conversation title, UUID, and timestamp.

**memories.json** — `conversations_memory` is a single markdown string (~6,794 chars in the sample export). Chunk by `##` heading. Each section becomes one chunk. `project_memories` stored as individual chunks.

**projects.json** — each project doc (markdown file attached to a project) becomes one chunk. Metadata includes project name.

**users.json** — skipped (no knowledge content).

### OpenAIAdapter

ChatGPT export uses a `mapping` object rather than a `chat_messages` array. Walk the mapping tree to reconstruct message order. Same output format as AnthropicAdapter — one chunk per user+assistant exchange.

### MarkdownAdapter

Chunk by heading (`#`, `##`, `###`). Each heading + its body text is one chunk. If no headings found, fall back to fixed-size chunks of ~500 words with 50-word overlap to preserve context across boundaries. Handles single files and directories (delegates directory walking to `DirectoryAdapter`).

### PDFAdapter / EpubAdapter

PDF: one chunk per page. Epub: one chunk per chapter. If a chunk exceeds 800 words, split on paragraph boundaries.

### SpreadsheetAdapter

CSV: each row becomes a chunk formatted as `"column: value, column: value, ..."`. XLSX: same, per sheet. For wide tables (>20 columns), group columns into logical sub-chunks.

### ImageAdapter

Pass image to mlx-vlm with prompt: `"Describe this image in detail, including any text visible."` Store the description as the chunk text. Only runs if a vision model is loaded; skips with a warning otherwise.

### DirectoryAdapter

Recursively walks the directory. For each file, tries all registered adapters in priority order. If no adapter matches, logs the filename as skipped. Returns the union of all chunks from all matched files.

---

## ImportService (`service.py`)

### Deduplication

Before storing each chunk, compute `SHA-256` of `chunk.text`. Query MemPalace for any existing document with `content_hash == hash` in metadata. If found — skip. Makes re-importing the same source idempotent.

### Chunking pass

Adapters return raw chunks. ImportService applies a second-pass size check: any chunk exceeding 800 words is split further using the strategy appropriate for its source type. Conversation chunks are never split — a user+assistant exchange is always one atomic unit.

### MemPalace storage

```python
add_drawer(
    collection,
    wing="loca",
    room=_classify_room(chunk.text),   # existing classifier
    content=chunk.text,
    source_file=f"{chunk.source}:{chunk.title}",
    chunk_index=i,
    agent="loca-import",
)
```

Metadata stored per chunk:
```python
{
    "source": "anthropic",
    "content_hash": "sha256...",
    "title": "Keychron M6 vs M7 comparison",
    "imported_at": "2026-04-16T10:00:00Z",
    "original_created_at": "2026-03-22T14:08:19Z",
}
```

### Progress events

`ImportService.run(path)` is an async generator yielding dicts:

```python
{"status": "detecting",   "path": "/path/to/export"}
{"status": "extracting",  "adapter": "anthropic", "total": 310}
{"status": "progress",    "current": 45, "total": 310, "skipped": 2}
{"status": "done",        "total": 310, "stored": 298, "skipped": 12}
{"status": "error",       "message": "..."}
```

CLI prints these as formatted output. API streams them as SSE. UI renders a progress bar.

### Import history

Each completed import writes a row to a new SQLite `import_history` table:

```sql
CREATE TABLE import_history (
    id       INTEGER PRIMARY KEY,
    source   TEXT NOT NULL,
    path     TEXT NOT NULL,
    stored   INTEGER NOT NULL,
    skipped  INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);
```

Exposed via `GET /api/import/history` for the UI to display past imports.

---

## CLI (`cli.py`)

```bash
# via make
make import path=../Anthropic-export-data-2026-04-06-12-07-38-batch-0000
make import path=../moatasem-context-full.md
make import path=~/Documents/books/

# directly
python -m src.importers.cli <path>
```

Output:
```
▶ Detected: anthropic (310 chunks from 103 conversations, 1 memory doc, 6 project docs)
  Extracting...    [████████████░░░░░░░░] 156/310
  Storing in MemPalace...
✓ Done — 298 stored, 12 skipped (duplicates)
```

`Makefile` addition:
```makefile
import:
    @[ "$(path)" ] || (echo "Usage: make import path=<path>"; exit 1)
    $(PYTHON) -m src.importers.cli $(path)
```

---

## API Endpoints

```
POST /api/import
Body:     { "path": "/absolute/path" }
Response: text/event-stream — progress events (SSE)

GET /api/import/history
Response: { "imports": [{ "source", "path", "stored", "skipped", "imported_at" }] }
```

Both endpoints require `_plugin_manager.memory_plugin` to be a `MemPalaceMemoryPlugin` (falls back gracefully with a 503 if MemPalace unavailable).

---

## UI

### Swift (Settings panel)

New "Import Knowledge" section in `SettingsView.swift`:

- **Choose File** button — `NSOpenPanel` (files only, all supported extensions)
- **Choose Folder** button — `NSOpenPanel` (directories only)
- Selected path shown in a text field
- **Import** button — calls `POST /api/import`, streams SSE response
- Progress bar + status line while importing
- Completion summary: "298 chunks imported from Anthropic export (12 duplicates skipped)"
- **Import History** list below — each past import with source icon, count, date, path

### HTML (Settings panel)

Same layout in `src/static/index.html`, new import section:

- Path text input + Browse button (HTML file input, folder input)
- Import button → `POST /api/import` with `fetch` + `EventSource` for SSE
- Progress bar rendered from SSE events
- Import history table populated from `GET /api/import/history`

Both panels call the same API endpoints — no logic duplication.

---

## Relationship to Vault Analyser

The importer handles the Obsidian vault as a one-shot ingestion (via `DirectoryAdapter` → `MarkdownAdapter` per file). This is sufficient to make vault content searchable in MemPalace.

The Vault Analyser's remaining unique value is:
- **Live sync** — file watcher that auto-ingests new/modified notes without manual re-import
- **Structural intelligence** — understands `[[wikilinks]]`, tags, frontmatter, daily notes, orphan detection
- **Bidirectional writes** — Loca can create or append to vault notes

The importer and vault analyser are complementary. The importer ships first; vault analyser becomes the live sync + structure layer built on top. The decision on vault analyser v2 scope is deferred until after the importer is in use.

---

## Dependencies

| Dep | Status | Used by |
|---|---|---|
| `pypdf` | already in `requirements.txt` | PDFAdapter |
| `trafilatura` | already in `requirements.txt` | WebAdapter |
| `pandas` | already available | SpreadsheetAdapter |
| `openpyxl` | new — add to `requirements.txt` | SpreadsheetAdapter (.xlsx) |
| `python-docx` | new — add to `requirements.txt` | DocxAdapter |
| `ebooklib` | new — add to `requirements.txt` | EpubAdapter |

---

## Testing

- **Unit tests** per adapter — mock filesystem, assert correct `Chunk` output for each source format
- **Deduplication test** — import same file twice, assert second run stores 0 new chunks
- **CLI test** — subprocess call, assert exit code 0 and stdout contains "Done"
- **API test** — mock ImportService, assert SSE event stream shape
- **Integration test** — import a small fixture export (included in `tests/fixtures/`), assert MemPalace contains expected chunks

---

## Out of Scope

- Audio ingestion via STT (separate feature — mlx-whisper pipeline complexity)
- Bidirectional writes to vault (vault analyser v2)
- Live sync / file watcher (vault analyser v2)
- Per-user import isolation (all imports land in single `loca` wing)
- Import scheduling / automation (future)
