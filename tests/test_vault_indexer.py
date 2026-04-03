"""
Tests for the Obsidian vault indexer (src/vault_indexer.py).

Run with: pytest tests/test_vault_indexer.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

import src.store as store_module
from src.vault_indexer import (
    detect_vaults,
    parse_note,
    scan_vault,
    validate_vault_path,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


# ── parse_note ───────────────────────────────────────────────────────────────


class TestParseNote:
    def test_basic_note(self):
        text = "# My Title\n\nSome content here with words."
        result = parse_note("notes/test.md", text)
        assert result["title"] == "My Title"
        assert result["word_count"] > 0
        assert result["content_hash"]

    def test_title_from_filename_when_no_h1(self):
        result = parse_note("notes/my-note.md", "No heading here, just text.")
        assert result["title"] == "my-note"

    def test_frontmatter_tags_inline(self):
        text = "---\ntags: [python, mlx, fine-tuning]\n---\n# Note\nContent"
        result = parse_note("test.md", text)
        assert "python" in result["tags"]
        assert "mlx" in result["tags"]
        assert "fine-tuning" in result["tags"]

    def test_frontmatter_tags_list(self):
        text = "---\ntags:\n  - alpha\n  - beta\n---\n# Note\nContent"
        result = parse_note("test.md", text)
        assert "alpha" in result["tags"]
        assert "beta" in result["tags"]

    def test_inline_tags(self):
        text = "# Note\nSome text with #machine-learning and #deep-learning tags."
        result = parse_note("test.md", text)
        assert "machine-learning" in result["tags"]
        assert "deep-learning" in result["tags"]

    def test_combined_frontmatter_and_inline_tags(self):
        text = "---\ntags: [fm-tag]\n---\n# Note\nInline #inline-tag here."
        result = parse_note("test.md", text)
        assert "fm-tag" in result["tags"]
        assert "inline-tag" in result["tags"]

    def test_no_duplicate_tags(self):
        text = "---\ntags: [shared]\n---\n# Note\nAlso #shared here."
        result = parse_note("test.md", text)
        assert result["tags"].count("shared") == 1

    def test_wiki_links(self):
        text = "# Note\nSee [[Other Note]] and [[Linked|alias]]."
        result = parse_note("test.md", text)
        links = [lk["to_note"] for lk in result["links"]]
        assert "Other Note" in links
        assert "Linked" in links

    def test_markdown_links_internal(self):
        text = "# Note\nSee [guide](setup-guide.md) for details."
        result = parse_note("test.md", text)
        links = [lk["to_note"] for lk in result["links"]]
        assert "setup-guide.md" in links

    def test_markdown_links_external_skipped(self):
        text = "# Note\nSee [Google](https://google.com)."
        result = parse_note("test.md", text)
        assert len(result["links"]) == 0

    def test_headings(self):
        text = "# Title\n## Section\n### Subsection\nContent."
        result = parse_note("test.md", text)
        assert len(result["headings"]) == 3
        assert result["headings"][0] == {"level": 1, "text": "Title"}
        assert result["headings"][1] == {"level": 2, "text": "Section"}

    def test_word_count_excludes_frontmatter(self):
        text = "---\ntags: [a, b, c, d, e]\n---\n# Note\nOne two three."
        result = parse_note("test.md", text)
        # "# Note" → 2 tokens + "One two three." → 3 = 5 words
        assert result["word_count"] == 5

    def test_content_hash_deterministic(self):
        text = "# Same content"
        r1 = parse_note("a.md", text)
        r2 = parse_note("b.md", text)
        assert r1["content_hash"] == r2["content_hash"]

    def test_no_frontmatter(self):
        text = "Just plain text with no frontmatter."
        result = parse_note("test.md", text)
        assert result["tags"] == []
        assert result["title"] == "test"


# ── validate_vault_path ─────────────────────────────────────────────────────


class TestValidateVaultPath:
    def test_valid_vault(self, tmp_path):
        (tmp_path / ".obsidian").mkdir()
        # tmp_path might not be under $HOME, so patch Path.home
        with patch("src.vault_indexer.Path.home", return_value=tmp_path.parent):
            assert validate_vault_path(str(tmp_path)) is None

    def test_nonexistent_path(self):
        err = validate_vault_path("/nonexistent/path/xyz")
        assert err is not None
        assert "not exist" in err.lower() or "not a directory" in err.lower()

    def test_no_obsidian_folder(self, tmp_path):
        with patch("src.vault_indexer.Path.home", return_value=tmp_path.parent):
            err = validate_vault_path(str(tmp_path))
        assert err is not None
        assert ".obsidian" in err

    def test_outside_home(self, tmp_path):
        (tmp_path / ".obsidian").mkdir()
        with patch("src.vault_indexer.Path.home", return_value=tmp_path / "fake_home"):
            err = validate_vault_path(str(tmp_path))
        assert err is not None
        assert "home" in err.lower()


# ── detect_vaults ────────────────────────────────────────────────────────────


class TestDetectVaults:
    def test_detect_from_obsidian_json(self, tmp_path):
        vault_dir = tmp_path / "my-vault"
        vault_dir.mkdir()
        config = {"vaults": {"abc123": {"path": str(vault_dir)}}}
        config_file = tmp_path / "obsidian.json"
        config_file.write_text(json.dumps(config))

        with patch("src.vault_indexer._obsidian_config_path", return_value=config_file):
            vaults = detect_vaults()
        assert len(vaults) == 1
        assert vaults[0]["name"] == "my-vault"
        assert vaults[0]["path"] == str(vault_dir)

    def test_no_config_file(self):
        with patch("src.vault_indexer._obsidian_config_path", return_value=None):
            assert detect_vaults() == []

    def test_vault_dir_missing(self, tmp_path):
        config = {"vaults": {"abc": {"path": "/nonexistent/vault"}}}
        config_file = tmp_path / "obsidian.json"
        config_file.write_text(json.dumps(config))

        with patch("src.vault_indexer._obsidian_config_path", return_value=config_file):
            assert detect_vaults() == []


# ── scan_vault ───────────────────────────────────────────────────────────────


class TestScanVault:
    def _make_vault(self, tmp_path, notes: dict[str, str]) -> Path:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        for rel_path, content in notes.items():
            full = vault / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        return vault

    def test_scan_indexes_notes(self, tmp_path):
        vault = self._make_vault(tmp_path, {
            "note1.md": "# Note One\nContent.",
            "sub/note2.md": "# Note Two\nMore content.",
        })
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            stats = scan_vault(str(vault))
        assert stats["total"] == 2
        assert stats["added"] == 2
        assert stats["errors"] == 0

    def test_scan_skips_unchanged(self, tmp_path):
        vault = self._make_vault(tmp_path, {"note.md": "# Note\nContent."})
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            scan_vault(str(vault))
            stats = scan_vault(str(vault))
        assert stats["skipped"] == 1
        assert stats["added"] == 0

    def test_scan_detects_updates(self, tmp_path):
        vault = self._make_vault(tmp_path, {"note.md": "# Note\nOriginal."})
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            scan_vault(str(vault))
            (vault / "note.md").write_text("# Note\nUpdated content.")
            stats = scan_vault(str(vault))
        assert stats["updated"] == 1

    def test_scan_removes_deleted_notes(self, tmp_path):
        vault = self._make_vault(tmp_path, {
            "keep.md": "# Keep\nContent.",
            "delete.md": "# Delete\nContent.",
        })
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            scan_vault(str(vault))
            (vault / "delete.md").unlink()
            stats = scan_vault(str(vault))
        assert stats["removed"] == 1

    def test_scan_skips_dotfiles_and_obsidian(self, tmp_path):
        vault = self._make_vault(tmp_path, {"visible.md": "# Visible"})
        (vault / ".obsidian" / "config.json").write_text("{}")
        (vault / ".hidden-note.md").write_text("# Hidden")
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            stats = scan_vault(str(vault))
        assert stats["total"] == 1

    def test_scan_invalid_vault(self, tmp_path):
        with pytest.raises(ValueError, match="not exist|not a directory"):
            scan_vault("/nonexistent/path")

    def test_scan_stores_links(self, tmp_path):
        vault = self._make_vault(tmp_path, {
            "a.md": "# A\nSee [[B]].",
            "b.md": "# B\nContent.",
        })
        with patch("src.vault_indexer.Path.home", return_value=tmp_path):
            scan_vault(str(vault))
        links = store_module.list_vault_links(str(vault.resolve()))
        assert len(links) == 1
        assert links[0]["to_note"] == "B"
