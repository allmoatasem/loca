"""
SQLite-backed store for conversations and memories.
DB lives at $LOCA_DATA_DIR/loca.db, or ~/Library/Application Support/Loca/data/loca.db
on macOS, or ~/.loca/data/loca.db on Linux/Windows.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import sqlite3
import time
import uuid
from pathlib import Path

try:
    import sqlite_vec
    _SQLITE_VEC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SQLITE_VEC_AVAILABLE = False

logger = logging.getLogger(__name__)


def _default_data_dir() -> Path:
    env = os.environ.get("LOCA_DATA_DIR")
    if env:
        return Path(env)
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Loca" / "data"
    return Path.home() / ".loca" / "data"


_DB_PATH = _default_data_dir() / "loca.db"


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    if _SQLITE_VEC_AVAILABLE:
        try:
            c.enable_load_extension(True)
            sqlite_vec.load(c)
            c.enable_load_extension(False)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"sqlite-vec failed to load, falling back to Python cosine: {exc}")
    _migrate(c)
    return c


def _migrate(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id       TEXT PRIMARY KEY,
        title    TEXT NOT NULL,
        created  REAL NOT NULL,
        updated  REAL NOT NULL,
        model    TEXT DEFAULT '',
        messages TEXT NOT NULL DEFAULT '[]',
        starred  INTEGER NOT NULL DEFAULT 0,
        folder   TEXT
    );
    CREATE TABLE IF NOT EXISTS memories (
        id      TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        created REAL NOT NULL,
        conv_id TEXT,
        type    TEXT NOT NULL DEFAULT 'user_fact'
    );
    """)
    # Idempotent column additions for existing databases
    conv_cols = {r[1] for r in c.execute("PRAGMA table_info(conversations)")}
    for col, defn in [
        ("starred",      "INTEGER NOT NULL DEFAULT 0"),
        ("folder",       "TEXT"),
        ("project_id",   "TEXT"),
        # Per-conversation LoRA override. NULL = use project (or base);
        # a value pins this conversation to a specific adapter so the
        # user can keep, e.g., a code-style adapter active in one
        # session while a writing-style sibling stays on the project's
        # default. Activated when the conversation is loaded.
        ("adapter_name", "TEXT"),
    ]:
        if col not in conv_cols:
            c.execute(f"ALTER TABLE conversations ADD COLUMN {col} {defn}")

    mem_cols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
    if "type" not in mem_cols:
        c.execute("ALTER TABLE memories ADD COLUMN type TEXT NOT NULL DEFAULT 'user_fact'")
    if "embedding" not in mem_cols:
        c.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")

    # Vault analyser tables
    c.executescript("""
    CREATE TABLE IF NOT EXISTS vault_notes (
        id           TEXT PRIMARY KEY,
        vault_path   TEXT NOT NULL,
        rel_path     TEXT NOT NULL,
        title        TEXT NOT NULL,
        word_count   INTEGER NOT NULL DEFAULT 0,
        tags         TEXT NOT NULL DEFAULT '[]',
        headings     TEXT NOT NULL DEFAULT '[]',
        created      REAL,
        modified     REAL,
        content_hash TEXT NOT NULL,
        indexed_at   REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_notes_rel ON vault_notes(vault_path, rel_path);

    CREATE TABLE IF NOT EXISTS vault_links (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        vault_path TEXT NOT NULL,
        from_note TEXT NOT NULL,
        to_note   TEXT NOT NULL,
        link_type TEXT NOT NULL DEFAULT 'wiki'
    );
    CREATE INDEX IF NOT EXISTS idx_vault_links_from ON vault_links(vault_path, from_note);
    CREATE INDEX IF NOT EXISTS idx_vault_links_to   ON vault_links(vault_path, to_note);
    """)

    # Vault analyser v2 — idempotent column additions
    vault_cols = {r[1] for r in c.execute("PRAGMA table_info(vault_notes)")}
    for col, defn in [
        ("is_daily_note", "BOOLEAN NOT NULL DEFAULT 0"),
        ("tasks",         "TEXT NOT NULL DEFAULT '[]'"),
        ("properties",    "TEXT NOT NULL DEFAULT '{}'"),
        ("body_snippet",  "TEXT NOT NULL DEFAULT ''"),
    ]:
        if col not in vault_cols:
            c.execute(f"ALTER TABLE vault_notes ADD COLUMN {col} {defn}")

    # Import history tracking
    c.execute("""
    CREATE TABLE IF NOT EXISTS import_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        path        TEXT NOT NULL,
        stored      INTEGER NOT NULL,
        skipped     INTEGER NOT NULL,
        imported_at TEXT NOT NULL
    );
    """)

    # Research Projects — the Research Partner feature. A project is a
    # scoped research effort; it has bookmarked items, freeform notes,
    # and optional scheduled watches that re-query the scope for new
    # findings.
    c.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id       TEXT PRIMARY KEY,
        title    TEXT NOT NULL,
        scope    TEXT NOT NULL DEFAULT '',
        notes    TEXT NOT NULL DEFAULT '',
        created  REAL NOT NULL,
        updated  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_items (
        id           TEXT PRIMARY KEY,
        project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        kind         TEXT NOT NULL,
        ref_id       TEXT,
        title        TEXT NOT NULL DEFAULT '',
        body         TEXT NOT NULL DEFAULT '',
        url          TEXT,
        content_hash TEXT NOT NULL DEFAULT '',
        created      REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_project_items_project
        ON project_items(project_id, created DESC);
    CREATE INDEX IF NOT EXISTS idx_project_items_hash
        ON project_items(project_id, content_hash);

    CREATE TABLE IF NOT EXISTS project_watches (
        id                 TEXT PRIMARY KEY,
        project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        sub_scope          TEXT NOT NULL,
        schedule_minutes   INTEGER NOT NULL,
        last_run           REAL,
        last_snapshot_hash TEXT,
        created            REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_project_watches_project
        ON project_watches(project_id);
    """)

    # Per-project LoRA adapter preference — activated when the project
    # becomes the focus so a writing-style project can override the
    # globally-active adapter for the duration of the session. Runs
    # after the projects table is created/guaranteed above.
    project_cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
    if "adapter_name" not in project_cols:
        c.execute("ALTER TABLE projects ADD COLUMN adapter_name TEXT")
    # Obsidian Watcher integration — when true, the project's recall pulls
    # from the app-level watched vaults instead of (or in addition to)
    # its per-project bookmarked items. Replaces the per-project Sync
    # Vault flow with a shared, always-fresh index.
    if "obsidian_source" not in project_cols:
        c.execute(
            "ALTER TABLE projects "
            "ADD COLUMN obsidian_source INTEGER NOT NULL DEFAULT 0",
        )

    # Obsidian Watcher — app-level registered vaults that the background
    # scanner keeps in sync. Replaces per-project "Sync Vault" with a
    # single source-of-truth index shared across all projects.
    c.executescript("""
    CREATE TABLE IF NOT EXISTS watched_vaults (
        path             TEXT PRIMARY KEY,
        name             TEXT NOT NULL DEFAULT '',
        enabled          INTEGER NOT NULL DEFAULT 1,
        scan_interval_s  INTEGER NOT NULL DEFAULT 300,
        last_scan_at     REAL,
        last_stats       TEXT NOT NULL DEFAULT '{}',
        created          REAL NOT NULL
    );
    """)

    c.commit()


# ── Conversations ─────────────────────────────────────────────────────────────

_MISSING = object()


def list_conversations(limit: int = 200) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, created, updated, model, starred, folder, "
            "project_id, adapter_name "
            "FROM conversations ORDER BY starred DESC, updated DESC LIMIT ?", (limit,)
        )]


def patch_conversation(
    conv_id: str, *,
    starred=_MISSING, folder=_MISSING, adapter_name=_MISSING,
) -> None:
    """Update starred/folder/adapter. Use _MISSING (default) to skip a
    field, None to clear (folder/adapter). Adapter override on a
    conversation pins it to a specific LoRA regardless of the
    project's binding."""
    parts: list[str] = []
    vals: list[object] = []
    if starred is not _MISSING:
        parts.append("starred = ?")
        vals.append(1 if starred else 0)
    if folder is not _MISSING:
        parts.append("folder = ?")
        vals.append(folder)
    if adapter_name is not _MISSING:
        parts.append("adapter_name = ?")
        vals.append(adapter_name)
    if not parts:
        return
    vals.append(conv_id)
    with _conn() as c:
        c.execute(f"UPDATE conversations SET {', '.join(parts)} WHERE id = ?", vals)
        c.commit()


def search_conversations(query: str, limit: int = 50) -> list[dict]:
    if not query.strip():
        return []
    like = f"%{query}%"
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, created, updated, model, starred, folder, "
            "project_id, adapter_name "
            "FROM conversations WHERE title LIKE ? OR messages LIKE ? "
            "ORDER BY starred DESC, updated DESC LIMIT ?",
            (like, like, limit),
        )]


def get_conversation(conv_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["messages"] = json.loads(d["messages"])
    return d


def save_conversation(conv_id: str | None, title: str, messages: list, model: str = "") -> str:
    now = time.time()
    cid = conv_id or str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            """INSERT INTO conversations (id, title, created, updated, model, messages)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title    = excluded.title,
                 updated  = excluded.updated,
                 model    = excluded.model,
                 messages = excluded.messages""",
            (cid, title, now, now, model, json.dumps(messages)),
        )
        c.commit()
    return cid


def delete_conversation(conv_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        c.commit()


# ── Memories ──────────────────────────────────────────────────────────────────

MEMORY_TYPES = ("user_fact", "knowledge", "correction")


def list_memories(
    limit: int = 200, type: str | None = None, offset: int = 0
) -> list[dict]:
    with _conn() as c:
        if type:
            return [dict(r) for r in c.execute(
                "SELECT * FROM memories WHERE type=? ORDER BY created DESC LIMIT ? OFFSET ?",
                (type, limit, offset),
            )]
        return [dict(r) for r in c.execute(
            "SELECT * FROM memories ORDER BY created DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )]


def count_memories(type: str | None = None) -> int:
    with _conn() as c:
        if type:
            row = c.execute("SELECT COUNT(*) FROM memories WHERE type=?", (type,)).fetchone()
        else:
            row = c.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0


def get_memory_position(mid: str) -> int | None:
    """Return the 0-based offset of a memory id in the default
    `ORDER BY created DESC` list, or None when the id isn't present.
    Lets the client skip-page directly to a deep-linked citation
    instead of walking the list 50 rows at a time — essential when
    a user has thousands of memories."""
    with _conn() as c:
        row = c.execute(
            "SELECT created FROM memories WHERE id = ?", (mid,),
        ).fetchone()
        if not row:
            return None
        created = row[0]
        count_row = c.execute(
            "SELECT COUNT(*) FROM memories WHERE created > ?", (created,),
        ).fetchone()
        return int(count_row[0]) if count_row else 0


def add_memory(content: str, conv_id: str | None = None, type: str = "user_fact") -> str:
    if type not in MEMORY_TYPES:
        type = "user_fact"
    mid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO memories (id, content, created, conv_id, type) VALUES (?, ?, ?, ?, ?)",
            (mid, content.strip(), time.time(), conv_id, type),
        )
        c.commit()
    return mid


def update_memory(mem_id: str, content: str) -> None:
    with _conn() as c:
        c.execute("UPDATE memories SET content=? WHERE id=?", (content.strip(), mem_id))
        c.commit()


def delete_memory(mem_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        c.commit()


def delete_memories_by_type(kind: str) -> int:
    """Bulk-delete every memory of a given type. Returns the row count.
    Used by the Memory panel's "delete all <kind>" affordance — the
    auto-extracted transcript pile can easily hit five digits, and
    deleting one-by-one is not a sane UX."""
    if kind not in MEMORY_TYPES:
        return 0
    with _conn() as c:
        cursor = c.execute("DELETE FROM memories WHERE type=?", (kind,))
        c.commit()
        return cursor.rowcount or 0


def delete_all_memories() -> int:
    """Wipe the memories table. Returns the row count. Explicit
    nuclear-option for users who want to start fresh."""
    with _conn() as c:
        cursor = c.execute("DELETE FROM memories")
        c.commit()
        return cursor.rowcount or 0


def get_memory_embedding(mem_id: str) -> bytes | None:
    with _conn() as c:
        row = c.execute(
            "SELECT embedding FROM memories WHERE id=?", (mem_id,)
        ).fetchone()
        return row["embedding"] if row else None


def set_memory_embedding(mem_id: str, blob: bytes | None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE memories SET embedding=? WHERE id=?", (blob, mem_id)
        )
        c.commit()


def list_memories_without_embeddings(limit: int = 200) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, content, type FROM memories WHERE embedding IS NULL "
            "ORDER BY created DESC LIMIT ?", (limit,)
        )]


def search_memories_semantic_sql(query_blob: bytes, limit: int = 5) -> list[dict]:
    """
    Find memories most similar to `query_blob` using sqlite-vec's vec_distance_cosine.

    Requires sqlite-vec to be loaded (see _conn). Falls back gracefully: returns []
    if the extension is unavailable, letting the caller use Python-side fallback.

    `query_blob` must be the same packed float32 BLOB format produced by
    BuiltinMemoryPlugin._pack().  Returns rows ordered by ascending cosine
    distance (closest first).
    """
    if not _SQLITE_VEC_AVAILABLE:
        return []
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, content, type, created, conv_id,
                   vec_distance_cosine(embedding, ?) AS distance
            FROM memories
            WHERE embedding IS NOT NULL
            ORDER BY distance ASC
            LIMIT ?
            """,
            (query_blob, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Vault ────────────────────────────────────────────────────────────────────


def upsert_vault_note(note: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO vault_notes
                   (id, vault_path, rel_path, title, word_count, tags, headings,
                    created, modified, content_hash, indexed_at,
                    is_daily_note, tasks, properties, body_snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(vault_path, rel_path) DO UPDATE SET
                    title        = excluded.title,
                    word_count   = excluded.word_count,
                    tags         = excluded.tags,
                    headings     = excluded.headings,
                    created      = excluded.created,
                    modified     = excluded.modified,
                    content_hash = excluded.content_hash,
                    indexed_at   = excluded.indexed_at,
                    is_daily_note = excluded.is_daily_note,
                    tasks        = excluded.tasks,
                    properties   = excluded.properties,
                    body_snippet = excluded.body_snippet""",
            (
                note["id"], note["vault_path"], note["rel_path"], note["title"],
                note["word_count"], json.dumps(note["tags"]), json.dumps(note["headings"]),
                note.get("created"), note.get("modified"),
                note["content_hash"], note["indexed_at"],
                1 if note.get("is_daily_note") else 0,
                json.dumps(note.get("tasks", [])),
                json.dumps(note.get("properties", {})),
                note.get("body_snippet", ""),
            ),
        )
        c.commit()


def replace_vault_links(vault_path: str, from_note: str, links: list[dict]) -> None:
    with _conn() as c:
        c.execute(
            "DELETE FROM vault_links WHERE vault_path=? AND from_note=?",
            (vault_path, from_note),
        )
        for lnk in links:
            c.execute(
                "INSERT INTO vault_links (vault_path, from_note, to_note, link_type) VALUES (?, ?, ?, ?)",
                (vault_path, from_note, lnk["to_note"], lnk.get("link_type", "wiki")),
            )
        c.commit()


def delete_vault_note(vault_path: str, rel_path: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM vault_notes WHERE vault_path=? AND rel_path=?", (vault_path, rel_path))
        c.execute("DELETE FROM vault_links WHERE vault_path=? AND from_note=?", (vault_path, rel_path))
        c.commit()


def clear_vault_index(vault_path: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM vault_notes WHERE vault_path=?", (vault_path,))
        c.execute("DELETE FROM vault_links WHERE vault_path=?", (vault_path,))
        c.commit()


def list_vault_paths() -> list[str]:
    """Distinct vault paths known to the index — used by link-discovery
    so it can search every vault without the caller having to pick one."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT vault_path FROM vault_notes ORDER BY vault_path"
        ).fetchall()
    return [r["vault_path"] for r in rows]


def list_vault_notes(vault_path: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM vault_notes WHERE vault_path=? ORDER BY rel_path", (vault_path,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"])
        d["headings"] = json.loads(d["headings"])
        d["tasks"] = json.loads(d.get("tasks") or "[]")
        d["properties"] = json.loads(d.get("properties") or "{}")
        result.append(d)
    return result


def list_vault_links(vault_path: str) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM vault_links WHERE vault_path=? ORDER BY from_note", (vault_path,)
        )]


def get_vault_note_content_hash(vault_path: str, rel_path: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT content_hash FROM vault_notes WHERE vault_path=? AND rel_path=?",
            (vault_path, rel_path),
        ).fetchone()
    return row["content_hash"] if row else None


# ── Obsidian Watcher ─────────────────────────────────────────────────────────


def _row_to_watched_vault(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["last_stats"] = json.loads(d.get("last_stats") or "{}")
    return d


def list_watched_vaults() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM watched_vaults ORDER BY created ASC"
        ).fetchall()
    return [_row_to_watched_vault(r) for r in rows]


def get_watched_vault(path: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM watched_vaults WHERE path=?", (path,)
        ).fetchone()
    return _row_to_watched_vault(row) if row else None


def upsert_watched_vault(
    path: str, *, name: str = "", scan_interval_s: int = 300,
) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO watched_vaults
                   (path, name, enabled, scan_interval_s, created)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   name            = excluded.name,
                   enabled         = 1,
                   scan_interval_s = excluded.scan_interval_s""",
            (path, name, max(60, scan_interval_s), now),
        )
        c.commit()


def set_watched_vault_enabled(path: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE watched_vaults SET enabled=? WHERE path=?",
            (1 if enabled else 0, path),
        )
        c.commit()


def delete_watched_vault(path: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM watched_vaults WHERE path=?", (path,))
        c.commit()


def mark_watched_vault_scanned(path: str, stats: dict) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE watched_vaults SET last_scan_at=?, last_stats=? WHERE path=?",
            (time.time(), json.dumps(stats), path),
        )
        c.commit()


def get_memories_context(limit_per_type: int = 10) -> str:
    """
    Return memories grouped by type, formatted for injection into system prompts.

    Three sections:
      - User facts: preferences, projects, personal context
      - Verified knowledge: facts confirmed via tool calls (web_search, web_fetch)
      - User corrections: rules the user has taught the model
    """
    facts = list_memories(limit=limit_per_type, type="user_fact")
    knowledge = list_memories(limit=limit_per_type, type="knowledge")
    corrections = list_memories(limit=limit_per_type, type="correction")

    if not facts and not knowledge and not corrections:
        return ""

    sections: list[str] = []
    if facts:
        lines = "\n".join(f"- {m['content']}" for m in reversed(facts))
        sections.append(f"User facts:\n{lines}")
    if knowledge:
        lines = "\n".join(f"- {m['content']}" for m in reversed(knowledge))
        sections.append(f"Verified knowledge (retrieved via search/fetch):\n{lines}")
    if corrections:
        lines = "\n".join(f"- {m['content']}" for m in reversed(corrections))
        sections.append(f"User corrections (rules to apply going forward):\n{lines}")

    body = "\n\n".join(sections)
    return f"<memory>\n{body}\n</memory>"


# ── Import History ───────────────────────────────────────────────────────────

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


# ── Research Projects ────────────────────────────────────────────────────────

PROJECT_ITEM_KINDS = ("conv", "memory", "vault_chunk", "web_url", "quote", "vault_sync")


def create_project(title: str, scope: str = "") -> str:
    now = time.time()
    pid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO projects (id, title, scope, notes, created, updated) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (pid, title, scope, now, now),
        )
        c.commit()
    return pid


def list_projects(limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT p.id, p.title, p.scope, p.notes, p.created, p.updated, "
            "       p.obsidian_source, "
            "       (SELECT COUNT(*) FROM project_items i WHERE i.project_id = p.id) AS item_count, "
            "       (SELECT COUNT(*) FROM conversations c WHERE c.project_id = p.id) AS conv_count "
            "FROM projects p ORDER BY p.updated DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["obsidian_source"] = bool(d.get("obsidian_source") or 0)
        result.append(d)
    return result


def get_project(project_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["obsidian_source"] = bool(d.get("obsidian_source") or 0)
    return d


def patch_project(
    project_id: str, *,
    title=_MISSING, scope=_MISSING, notes=_MISSING, adapter_name=_MISSING,
    obsidian_source=_MISSING,
) -> None:
    parts: list[str] = []
    vals: list[object] = []
    if title is not _MISSING:
        parts.append("title = ?")
        vals.append(title)
    if scope is not _MISSING:
        parts.append("scope = ?")
        vals.append(scope)
    if notes is not _MISSING:
        parts.append("notes = ?")
        vals.append(notes)
    if adapter_name is not _MISSING:
        parts.append("adapter_name = ?")
        vals.append(adapter_name)
    if obsidian_source is not _MISSING:
        parts.append("obsidian_source = ?")
        vals.append(1 if obsidian_source else 0)
    if not parts:
        return
    parts.append("updated = ?")
    vals.append(time.time())
    vals.append(project_id)
    with _conn() as c:
        c.execute(
            f"UPDATE projects SET {', '.join(parts)} WHERE id = ?", vals,
        )
        c.commit()


def delete_project(project_id: str) -> None:
    with _conn() as c:
        # Foreign-key cascades handle project_items / project_watches.
        # conversations.project_id is a plain TEXT column, so clear it
        # manually to avoid dangling references.
        c.execute(
            "UPDATE conversations SET project_id = NULL WHERE project_id = ?",
            (project_id,),
        )
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        c.commit()


def add_project_item(
    project_id: str,
    *,
    kind: str,
    title: str = "",
    body: str = "",
    ref_id: str | None = None,
    url: str | None = None,
    content_hash: str = "",
) -> str | None:
    """Insert a project item. Dedupes by (project_id, content_hash) when a
    hash is supplied — returns None when the row already exists so callers
    can distinguish "stored fresh" from "already present"."""
    if kind not in PROJECT_ITEM_KINDS:
        raise ValueError(f"unknown project item kind: {kind}")
    with _conn() as c:
        if content_hash:
            row = c.execute(
                "SELECT id FROM project_items WHERE project_id=? AND content_hash=?",
                (project_id, content_hash),
            ).fetchone()
            if row:
                return None
        iid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO project_items "
            "(id, project_id, kind, ref_id, title, body, url, content_hash, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, project_id, kind, ref_id, title, body, url, content_hash, time.time()),
        )
        c.execute("UPDATE projects SET updated = ? WHERE id = ?", (time.time(), project_id))
        c.commit()
        return iid


def list_project_items(
    project_id: str, *, kind: str | None = None, limit: int = 200, offset: int = 0,
) -> list[dict]:
    with _conn() as c:
        if kind:
            rows = c.execute(
                "SELECT * FROM project_items WHERE project_id=? AND kind=? "
                "ORDER BY created DESC LIMIT ? OFFSET ?",
                (project_id, kind, limit, offset),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM project_items WHERE project_id=? "
                "ORDER BY created DESC LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_project_items(project_id: str) -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM project_items WHERE project_id=?", (project_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def delete_project_item(item_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM project_items WHERE id=?", (item_id,))
        c.commit()


def set_conversation_project(conv_id: str, project_id: str | None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET project_id=? WHERE id=?",
            (project_id, conv_id),
        )
        c.commit()


def list_project_conversations(project_id: str, limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, title, created, updated, model, starred, folder, project_id "
            "FROM conversations WHERE project_id=? "
            "ORDER BY starred DESC, updated DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def create_project_watch(
    project_id: str, sub_scope: str, schedule_minutes: int,
) -> str:
    wid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO project_watches "
            "(id, project_id, sub_scope, schedule_minutes, last_run, last_snapshot_hash, created) "
            "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
            (wid, project_id, sub_scope, schedule_minutes, time.time()),
        )
        c.commit()
    return wid


def list_project_watches(project_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM project_watches WHERE project_id=? ORDER BY created DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_due_watches(now: float | None = None) -> list[dict]:
    """Watches whose last_run was more than schedule_minutes ago (or never)."""
    now = now or time.time()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM project_watches "
            "WHERE last_run IS NULL "
            "   OR (? - last_run) >= (schedule_minutes * 60)",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_watch_ran(watch_id: str, snapshot_hash: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE project_watches SET last_run=?, last_snapshot_hash=? WHERE id=?",
            (time.time(), snapshot_hash, watch_id),
        )
        c.commit()


def delete_project_watch(watch_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM project_watches WHERE id=?", (watch_id,))
        c.commit()
