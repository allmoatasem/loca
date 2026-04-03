"""
Tests for the vault analyser (src/vault_analyser.py).

Run with: pytest tests/test_vault_analyser.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

import src.store as store_module
from src.vault_analyser import (
    find_broken_links,
    find_dead_ends,
    find_link_suggestions,
    find_orphan_notes,
    find_tag_orphans,
    full_analysis,
    vault_stats,
)
from src.vault_indexer import scan_vault


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


def _make_vault(tmp_path, notes: dict[str, str]) -> str:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    for rel_path, content in notes.items():
        full = vault / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    with patch("src.vault_indexer.Path.home", return_value=tmp_path):
        scan_vault(str(vault))
    return str(vault.resolve())


# ── vault_stats ──────────────────────────────────────────────────────────────


class TestVaultStats:
    def test_empty_vault(self, tmp_path):
        vault = tmp_path / "empty"
        vault.mkdir()
        stats = vault_stats(str(vault))
        assert stats["note_count"] == 0
        assert stats["link_count"] == 0

    def test_basic_stats(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "---\ntags: [python]\n---\n# A\nOne two three.",
            "b.md": "# B\nSee [[A]]. Four five.",
            "sub/c.md": "# C\nContent with #python tag.",
        })
        stats = vault_stats(vpath)
        assert stats["note_count"] == 3
        assert stats["link_count"] == 1  # b -> a
        assert stats["total_words"] > 0
        assert stats["folder_count"] == 1  # "sub"
        assert stats["tag_count"] >= 1  # "python"

    def test_top_tags(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\n#shared #unique-a",
            "b.md": "# B\n#shared #unique-b",
        })
        stats = vault_stats(vpath)
        top = {t["tag"]: t["count"] for t in stats["top_tags"]}
        assert top["shared"] == 2


# ── find_orphan_notes ────────────────────────────────────────────────────────


class TestOrphans:
    def test_orphan_detection(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "linked.md": "# Linked\nContent.",
            "linker.md": "# Linker\nSee [[linked]].",
            "orphan.md": "# Orphan\nNobody links here.",
        })
        orphans = find_orphan_notes(vpath)
        orphan_paths = [o["rel_path"] for o in orphans]
        assert "orphan.md" in orphan_paths
        # linker is also an orphan (nobody links TO it)
        assert "linker.md" in orphan_paths
        # linked is NOT an orphan
        assert "linked.md" not in orphan_paths

    def test_no_orphans(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\nSee [[b]].",
            "b.md": "# B\nSee [[a]].",
        })
        orphans = find_orphan_notes(vpath)
        assert len(orphans) == 0


# ── find_dead_ends ───────────────────────────────────────────────────────────


class TestDeadEnds:
    def test_dead_end_detection(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "active.md": "# Active\nSee [[target]].",
            "dead.md": "# Dead End\nNo links here.",
            "target.md": "# Target\nAlso no outgoing.",
        })
        dead_ends = find_dead_ends(vpath)
        dead_paths = [d["rel_path"] for d in dead_ends]
        assert "dead.md" in dead_paths
        assert "target.md" in dead_paths
        assert "active.md" not in dead_paths


# ── find_broken_links ────────────────────────────────────────────────────────


class TestBrokenLinks:
    def test_broken_link_detection(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\nSee [[nonexistent]] and [[b]].",
            "b.md": "# B\nContent.",
        })
        broken = find_broken_links(vpath)
        targets = [b["to_note"] for b in broken]
        assert "nonexistent" in targets
        assert "b" not in targets  # b exists

    def test_no_broken_links(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\nSee [[b]].",
            "b.md": "# B\nContent.",
        })
        assert len(find_broken_links(vpath)) == 0


# ── find_tag_orphans ─────────────────────────────────────────────────────────


class TestTagOrphans:
    def test_single_use_tags(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\n#shared #only-in-a",
            "b.md": "# B\n#shared #only-in-b",
        })
        orphans = find_tag_orphans(vpath)
        tags = [o["tag"] for o in orphans]
        assert "only-in-a" in tags
        assert "only-in-b" in tags
        assert "shared" not in tags


# ── find_link_suggestions ────────────────────────────────────────────────────


class TestLinkSuggestions:
    def test_suggests_links_for_shared_tags(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\n#machine-learning #python",
            "b.md": "# B\n#machine-learning #python",
            "c.md": "# C\n#unrelated",
        })
        suggestions = find_link_suggestions(vpath)
        assert len(suggestions) >= 1
        pair = suggestions[0]
        titles = {pair["note_a"]["title"], pair["note_b"]["title"]}
        assert titles == {"A", "B"}
        assert "machine-learning" in pair["shared_tags"]

    def test_no_suggestions_when_already_linked(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\n#tag See [[b]].",
            "b.md": "# B\n#tag Content.",
        })
        suggestions = find_link_suggestions(vpath)
        assert len(suggestions) == 0

    def test_no_suggestions_for_unrelated_notes(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\n#alpha",
            "b.md": "# B\n#beta",
        })
        assert len(find_link_suggestions(vpath)) == 0


# ── full_analysis ────────────────────────────────────────────────────────────


class TestFullAnalysis:
    def test_returns_all_sections(self, tmp_path):
        vpath = _make_vault(tmp_path, {
            "a.md": "# A\nSee [[b]] and [[missing]].",
            "b.md": "# B\n#tag Content.",
            "orphan.md": "# Orphan\n#tag Alone.",
        })
        result = full_analysis(vpath)
        assert "stats" in result
        assert "orphans" in result
        assert "dead_ends" in result
        assert "broken_links" in result
        assert "tag_orphans" in result
        assert "link_suggestions" in result
        assert result["stats"]["note_count"] == 3
        assert len(result["broken_links"]) >= 1  # "missing"
