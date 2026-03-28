"""
SQLite-backed store for conversations and memories.
DB lives at <project_root>/data/loca.db
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "loca.db"


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
        messages TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS memories (
        id      TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        created REAL NOT NULL,
        conv_id TEXT
    );
    """)
    c.commit()


# ── Conversations ─────────────────────────────────────────────────────────────

def list_conversations(limit: int = 100) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, created, updated, model FROM conversations "
            "ORDER BY updated DESC LIMIT ?", (limit,)
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

def list_memories(limit: int = 200) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM memories ORDER BY created DESC LIMIT ?", (limit,)
        )]


def add_memory(content: str, conv_id: str | None = None) -> str:
    mid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO memories (id, content, created, conv_id) VALUES (?, ?, ?, ?)",
            (mid, content.strip(), time.time(), conv_id),
        )
        c.commit()
    return mid


def delete_memory(mem_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        c.commit()


def get_memories_context(limit: int = 15) -> str:
    """Return the most recent memories formatted for injection into system prompts."""
    mems = list_memories(limit)
    if not mems:
        return ""
    lines = "\n".join(f"- {m['content']}" for m in reversed(mems))
    return f"<memory>\n{lines}\n</memory>"
