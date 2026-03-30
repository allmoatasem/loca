"""
SQLite-backed store for conversations and memories.
DB lives at $LOCA_DATA_DIR/loca.db, or ~/Library/Application Support/Loca/data/loca.db
on macOS, or ~/.loca/data/loca.db on Linux/Windows.
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import time
import uuid
from pathlib import Path


def _default_data_dir() -> Path:
    env = os.environ.get("LOCA_DATA_DIR")
    if env:
        return Path(env)
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Loca" / "data"
    return Path.home() / ".loca" / "data"


_DB_PATH = _default_data_dir() / "loca.db"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
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


def list_memories(limit: int = 200, type: str | None = None) -> list[dict]:
    with _conn() as c:
        if type:
            return [dict(r) for r in c.execute(
                "SELECT * FROM memories WHERE type=? ORDER BY created DESC LIMIT ?", (type, limit)
            )]
        return [dict(r) for r in c.execute(
            "SELECT * FROM memories ORDER BY created DESC LIMIT ?", (limit,)
        )]


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
