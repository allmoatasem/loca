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
    for col, defn in [("starred", "INTEGER NOT NULL DEFAULT 0"), ("folder", "TEXT")]:
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

    c.commit()


# ── Conversations ─────────────────────────────────────────────────────────────

_MISSING = object()


def list_conversations(limit: int = 200) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, created, updated, model, starred, folder "
            "FROM conversations ORDER BY starred DESC, updated DESC LIMIT ?", (limit,)
        )]


def patch_conversation(conv_id: str, *, starred=_MISSING, folder=_MISSING) -> None:
    """Update starred/folder. Use _MISSING (default) to skip a field, None to clear folder."""
    parts: list[str] = []
    vals: list[object] = []
    if starred is not _MISSING:
        parts.append("starred = ?")
        vals.append(1 if starred else 0)
    if folder is not _MISSING:
        parts.append("folder = ?")
        vals.append(folder)
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
            "SELECT id, title, created, updated, model, starred, folder "
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
