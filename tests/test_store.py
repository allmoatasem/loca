"""
Tests for the SQLite store.

Run with: pytest tests/test_store.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

import src.store as store_module


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Redirect the store to a temp DB for every test."""
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


# ── Conversations ─────────────────────────────────────────────────────────────

def test_save_and_get_conversation():
    cid = store_module.save_conversation(None, "Test conv", [{"role": "user", "content": "hi"}], "gpt")
    conv = store_module.get_conversation(cid)
    assert conv is not None
    assert conv["title"] == "Test conv"
    assert len(conv["messages"]) == 1


def test_get_nonexistent_conversation():
    assert store_module.get_conversation("does-not-exist") is None


def test_list_conversations_empty():
    assert store_module.list_conversations() == []


def test_list_conversations_ordered_by_updated():
    store_module.save_conversation(None, "A", [], "m")
    store_module.save_conversation(None, "B", [], "m")
    convs = store_module.list_conversations()
    assert len(convs) == 2
    # Most recently updated first
    assert convs[0]["title"] == "B"


def test_delete_conversation():
    cid = store_module.save_conversation(None, "Del me", [], "m")
    store_module.delete_conversation(cid)
    assert store_module.get_conversation(cid) is None


def test_search_conversations_by_title():
    store_module.save_conversation(None, "Oscar winners 2026", [], "m")
    store_module.save_conversation(None, "Python tips", [], "m")
    results = store_module.search_conversations("Oscar")
    assert len(results) == 1
    assert results[0]["title"] == "Oscar winners 2026"


def test_search_conversations_empty_query():
    store_module.save_conversation(None, "X", [], "m")
    results = store_module.search_conversations("")
    assert results == []


def test_patch_starred():
    cid = store_module.save_conversation(None, "Star me", [], "m")
    store_module.patch_conversation(cid, starred=True)
    conv = store_module.get_conversation(cid)
    assert conv["starred"] == 1


def test_patch_folder():
    cid = store_module.save_conversation(None, "Folder me", [], "m")
    store_module.patch_conversation(cid, folder="work")
    conv = store_module.get_conversation(cid)
    assert conv["folder"] == "work"


def test_patch_clear_folder():
    cid = store_module.save_conversation(None, "X", [], "m")
    store_module.patch_conversation(cid, folder="work")
    store_module.patch_conversation(cid, folder=None)
    conv = store_module.get_conversation(cid)
    assert conv["folder"] is None


def test_conversation_update_preserves_starred():
    cid = store_module.save_conversation(None, "Y", [], "m")
    store_module.patch_conversation(cid, starred=True)
    # Re-saving updates title/messages but not starred
    store_module.save_conversation(cid, "Y updated", [{"role": "user", "content": "x"}], "m")
    conv = store_module.get_conversation(cid)
    assert conv["title"] == "Y updated"


# ── Memories ──────────────────────────────────────────────────────────────────

def test_add_and_list_memory():
    mid = store_module.add_memory("I like Python", type="user_fact")
    mems = store_module.list_memories()
    assert len(mems) == 1
    assert mems[0]["id"] == mid
    assert mems[0]["content"] == "I like Python"
    assert mems[0]["type"] == "user_fact"


def test_add_knowledge_memory():
    store_module.add_memory("2026 Best Picture: One Battle After Another", type="knowledge")
    mems = store_module.list_memories(type="knowledge")
    assert len(mems) == 1
    assert mems[0]["type"] == "knowledge"


def test_add_correction_memory():
    store_module.add_memory("Trust web_search results over training cutoff", type="correction")
    mems = store_module.list_memories(type="correction")
    assert len(mems) == 1


def test_list_memories_by_type_filters():
    store_module.add_memory("user fact", type="user_fact")
    store_module.add_memory("knowledge", type="knowledge")
    store_module.add_memory("correction", type="correction")

    assert len(store_module.list_memories(type="user_fact")) == 1
    assert len(store_module.list_memories(type="knowledge")) == 1
    assert len(store_module.list_memories(type="correction")) == 1
    assert len(store_module.list_memories()) == 3


def test_delete_memory():
    mid = store_module.add_memory("delete me")
    store_module.delete_memory(mid)
    assert store_module.list_memories() == []


def test_update_memory():
    mid = store_module.add_memory("old content")
    store_module.update_memory(mid, "new content")
    mems = store_module.list_memories()
    assert mems[0]["content"] == "new content"


def test_invalid_type_defaults_to_user_fact():
    store_module.add_memory("x", type="nonsense")
    mems = store_module.list_memories()
    assert mems[0]["type"] == "user_fact"


def test_get_memories_context_empty():
    assert store_module.get_memories_context() == ""


def test_get_memories_context_sections():
    store_module.add_memory("Uses M3 Ultra", type="user_fact")
    store_module.add_memory("2026 Oscars verified", type="knowledge")
    store_module.add_memory("Trust search results", type="correction")

    ctx = store_module.get_memories_context()
    assert "<memory>" in ctx
    assert "User facts:" in ctx
    assert "Verified knowledge" in ctx
    assert "User corrections" in ctx
    assert "Uses M3 Ultra" in ctx
    assert "2026 Oscars verified" in ctx
    assert "Trust search results" in ctx


def test_get_memories_context_only_user_facts():
    store_module.add_memory("Fact only", type="user_fact")
    ctx = store_module.get_memories_context()
    assert "User facts:" in ctx
    assert "Verified knowledge" not in ctx
    assert "User corrections" not in ctx


# ── Vault ────────────────────────────────────────────────────────────────────

def test_upsert_and_list_vault_notes():
    note = {
        "id": "n1", "vault_path": "/v", "rel_path": "test.md", "title": "Test",
        "word_count": 10, "tags": ["python"], "headings": [{"level": 1, "text": "Test"}],
        "created": 1.0, "modified": 2.0, "content_hash": "abc123", "indexed_at": 3.0,
    }
    store_module.upsert_vault_note(note)
    notes = store_module.list_vault_notes("/v")
    assert len(notes) == 1
    assert notes[0]["title"] == "Test"
    assert notes[0]["tags"] == ["python"]


def test_upsert_vault_note_updates_on_conflict():
    note = {
        "id": "n1", "vault_path": "/v", "rel_path": "test.md", "title": "Old",
        "word_count": 5, "tags": [], "headings": [],
        "created": 1.0, "modified": 2.0, "content_hash": "hash1", "indexed_at": 3.0,
    }
    store_module.upsert_vault_note(note)
    note["id"] = "n2"  # different id but same vault_path+rel_path
    note["title"] = "Updated"
    note["content_hash"] = "hash2"
    store_module.upsert_vault_note(note)
    notes = store_module.list_vault_notes("/v")
    assert len(notes) == 1
    assert notes[0]["title"] == "Updated"


def test_replace_vault_links():
    store_module.replace_vault_links("/v", "a.md", [
        {"to_note": "b", "link_type": "wiki"},
        {"to_note": "c.md", "link_type": "markdown"},
    ])
    links = store_module.list_vault_links("/v")
    assert len(links) == 2
    # Replace with new set
    store_module.replace_vault_links("/v", "a.md", [{"to_note": "d", "link_type": "wiki"}])
    links = store_module.list_vault_links("/v")
    assert len(links) == 1
    assert links[0]["to_note"] == "d"


def test_delete_vault_note():
    note = {
        "id": "n1", "vault_path": "/v", "rel_path": "del.md", "title": "Del",
        "word_count": 1, "tags": [], "headings": [],
        "created": 1.0, "modified": 2.0, "content_hash": "h", "indexed_at": 3.0,
    }
    store_module.upsert_vault_note(note)
    store_module.replace_vault_links("/v", "del.md", [{"to_note": "x"}])
    store_module.delete_vault_note("/v", "del.md")
    assert store_module.list_vault_notes("/v") == []
    assert store_module.list_vault_links("/v") == []


def test_get_vault_note_content_hash():
    note = {
        "id": "n1", "vault_path": "/v", "rel_path": "h.md", "title": "H",
        "word_count": 1, "tags": [], "headings": [],
        "created": 1.0, "modified": 2.0, "content_hash": "myhash", "indexed_at": 3.0,
    }
    store_module.upsert_vault_note(note)
    assert store_module.get_vault_note_content_hash("/v", "h.md") == "myhash"
    assert store_module.get_vault_note_content_hash("/v", "missing.md") is None


def test_clear_vault_index():
    note = {
        "id": "n1", "vault_path": "/v", "rel_path": "c.md", "title": "C",
        "word_count": 1, "tags": [], "headings": [],
        "created": 1.0, "modified": 2.0, "content_hash": "h", "indexed_at": 3.0,
    }
    store_module.upsert_vault_note(note)
    store_module.replace_vault_links("/v", "c.md", [{"to_note": "x"}])
    store_module.clear_vault_index("/v")
    assert store_module.list_vault_notes("/v") == []
    assert store_module.list_vault_links("/v") == []


def test_vault_notes_isolated_by_path():
    for vp in ["/v1", "/v2"]:
        note = {
            "id": f"n-{vp}", "vault_path": vp, "rel_path": "same.md", "title": vp,
            "word_count": 1, "tags": [], "headings": [],
            "created": 1.0, "modified": 2.0, "content_hash": "h", "indexed_at": 3.0,
        }
        store_module.upsert_vault_note(note)
    assert len(store_module.list_vault_notes("/v1")) == 1
    assert len(store_module.list_vault_notes("/v2")) == 1


# ── Import history ────────────────────────────────────────────────────────────

def test_add_and_list_import_record():
    store_module.add_import_record(source="anthropic", path="/tmp/export", stored=10, skipped=2)
    history = store_module.list_import_history()
    assert any(r["source"] == "anthropic" and r["stored"] == 10 for r in history)
