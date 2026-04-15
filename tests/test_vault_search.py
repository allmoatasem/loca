"""
Tests for vault analyser v2: TF-IDF search, daily notes, tasks, properties.

Run with: pytest tests/test_vault_search.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

import src.store as store_module
from src.vault_indexer import parse_note
from src.vault_search import (
    build_tfidf_index,
    clear_vault_search_cache,
    semantic_search,
)

# ── DB isolation ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_note(vault_path: str, rel_path: str, title: str, snippet: str = "", tags: list | None = None) -> None:
    import time
    import uuid
    store_module.upsert_vault_note({
        "id": str(uuid.uuid4()),
        "vault_path": vault_path,
        "rel_path": rel_path,
        "title": title,
        "word_count": len(snippet.split()),
        "tags": tags or [],
        "headings": [],
        "created": time.time(),
        "modified": time.time(),
        "content_hash": "abc",
        "indexed_at": time.time(),
        "is_daily_note": False,
        "tasks": [],
        "properties": {},
        "body_snippet": snippet,
    })


# ── semantic_search ───────────────────────────────────────────────────────────

class TestSemanticSearch:
    def test_semantic_search_returns_results(self, tmp_path):
        vp = str(tmp_path / "vault")
        _make_note(vp, "python.md", "Python Programming", "loops functions classes variables", ["python"])
        _make_note(vp, "cooking.md", "Cooking Recipes", "bake fry boil steam rice pasta", ["food"])
        clear_vault_search_cache(vp)
        results = semantic_search(vp, "python loops functions")
        assert len(results) >= 1
        assert results[0]["rel_path"] == "python.md"

    def test_semantic_search_empty_vault(self, tmp_path):
        vp = str(tmp_path / "empty")
        clear_vault_search_cache(vp)
        results = semantic_search(vp, "anything")
        assert results == []

    def test_semantic_search_result_fields(self, tmp_path):
        vp = str(tmp_path / "vault2")
        _make_note(vp, "ml.md", "Machine Learning", "neural networks training gradient", ["ml", "ai"])
        clear_vault_search_cache(vp)
        results = semantic_search(vp, "neural networks")
        assert len(results) == 1
        r = results[0]
        assert "rel_path" in r
        assert "title" in r
        assert "score" in r
        assert "snippet" in r
        assert "tags" in r
        assert "is_daily_note" in r
        assert "tasks_count" in r
        assert 0.0 < r["score"] <= 1.0

    def test_semantic_search_empty_query(self, tmp_path):
        vp = str(tmp_path / "vault3")
        _make_note(vp, "note.md", "Some Note", "content here")
        clear_vault_search_cache(vp)
        assert semantic_search(vp, "") == []
        assert semantic_search(vp, "   ") == []

    def test_semantic_search_limit(self, tmp_path):
        vp = str(tmp_path / "vault4")
        for i in range(10):
            _make_note(vp, f"note{i}.md", f"Note {i}", f"machine learning model training iteration {i}")
        clear_vault_search_cache(vp)
        results = semantic_search(vp, "machine learning", limit=3)
        assert len(results) <= 3

    def test_build_tfidf_index_structure(self, tmp_path):
        vp = str(tmp_path / "vault5")
        _make_note(vp, "a.md", "Alpha", "first document content", ["alpha"])
        _make_note(vp, "b.md", "Beta", "second document content", ["beta"])
        vec, matrix, notes = build_tfidf_index(vp)
        assert matrix.shape[0] == 2
        assert len(notes) == 2


# ── parse_note — daily notes ──────────────────────────────────────────────────

class TestParseDailyNote:
    def test_daily_note_detected(self):
        result = parse_note("2024-01-15.md", "# January 15\nToday I did things.")
        assert result["is_daily_note"] is True

    def test_daily_note_in_subfolder(self):
        result = parse_note("daily/2024-03-20.md", "# March 20\nContent.")
        assert result["is_daily_note"] is True

    def test_non_daily_note_not_detected(self):
        result = parse_note("notes/my-ideas.md", "# Ideas\nSome ideas here.")
        assert result["is_daily_note"] is False

    def test_partial_date_not_detected(self):
        result = parse_note("notes/2024-review.md", "# Review\nContent.")
        assert result["is_daily_note"] is False


# ── parse_note — task extraction ─────────────────────────────────────────────

class TestParseTaskExtraction:
    def test_open_task_extracted(self):
        result = parse_note("test.md", "# Note\n- [ ] Buy groceries\n- [ ] Call dentist")
        assert len(result["tasks"]) == 2
        assert result["tasks"][0]["text"] == "Buy groceries"
        assert result["tasks"][0]["completed"] is False

    def test_completed_task_extracted(self):
        result = parse_note("test.md", "# Note\n- [x] Done task\n- [ ] Pending task")
        done = [t for t in result["tasks"] if t["completed"]]
        pending = [t for t in result["tasks"] if not t["completed"]]
        assert len(done) == 1
        assert done[0]["text"] == "Done task"
        assert len(pending) == 1

    def test_task_line_numbers(self):
        text = "# Note\nline 2\n- [ ] Task on line 3\nline 4"
        result = parse_note("test.md", text)
        assert result["tasks"][0]["line"] == 3

    def test_no_tasks(self):
        result = parse_note("test.md", "# Note\nJust regular content, no tasks.")
        assert result["tasks"] == []

    def test_tasks_have_required_fields(self):
        result = parse_note("test.md", "- [x] My task")
        assert all("text" in t and "completed" in t and "line" in t for t in result["tasks"])


# ── parse_note — frontmatter properties ──────────────────────────────────────

class TestParseProperties:
    def test_scalar_properties_extracted(self):
        text = "---\nauthor: Alice\nstatus: draft\ndate: 2024-01-01\n---\n# Note\nContent"
        result = parse_note("test.md", text)
        assert result["properties"]["author"] == "Alice"
        assert result["properties"]["status"] == "draft"

    def test_tags_not_in_properties(self):
        text = "---\ntags: [a, b]\nauthor: Bob\n---\n# Note"
        result = parse_note("test.md", text)
        assert "tags" not in result["properties"]
        assert result["properties"].get("author") == "Bob"

    def test_no_frontmatter_empty_properties(self):
        result = parse_note("test.md", "# Note\nNo frontmatter here.")
        assert result["properties"] == {}

    def test_empty_frontmatter_empty_properties(self):
        result = parse_note("test.md", "---\ntags: [a]\n---\n# Note")
        assert result["properties"] == {}


# ── parse_note — body_snippet ─────────────────────────────────────────────────

class TestParseBodySnippet:
    def test_body_snippet_excludes_frontmatter(self):
        text = "---\nauthor: Alice\n---\n# Title\nActual content here."
        result = parse_note("test.md", text)
        assert "author" not in result["body_snippet"]
        assert "Title" in result["body_snippet"] or "content" in result["body_snippet"]

    def test_body_snippet_truncated_to_500(self):
        body = "x " * 400  # 800 chars
        text = f"# Note\n{body}"
        result = parse_note("test.md", text)
        assert len(result["body_snippet"]) <= 500

    def test_body_snippet_short_note_not_truncated(self):
        text = "# Note\nShort content."
        result = parse_note("test.md", text)
        assert "Short content." in result["body_snippet"]
