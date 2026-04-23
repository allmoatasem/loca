"""Unit tests for `src.proxy._build_citations`.

The citation builder is the bit that turns the orchestrator's mixed
retrieval pool (memories, project items, obsidian notes, web hits)
into the structured array the chat bubble renders. If this function
doesn't populate `title` / `snippet` / `kind` correctly, every pill
in the UI falls back to a "MISSING" placeholder — which is exactly
the regression the user hit. These tests pin the expected shape so
it can't silently drift again.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.proxy import _build_citations  # noqa: E402


def test_real_memory_falls_back_to_content_for_title_and_snippet():
    """MemPalace recall returns `{index, id, score, content}` — no
    `title` or `snippet`. The builder must synthesise both from
    `content` so the popover has something to display."""
    out = _build_citations([{
        "index": 1,
        "id": "d3c27e63-9e2c-4c4a-8d7c-5c11a8c9f1aa",
        "score": 0.87,
        "content": "The Umayyad conquest of Iberia began in 711 CE when Tariq ibn Ziyad crossed the strait.",
    }])
    assert len(out) == 1
    c = out[0]
    assert c["idx"] == 1
    assert c["kind"] == "memory"
    assert c["title"].startswith("The Umayyad conquest")
    assert "711 CE" in c["snippet"]
    assert c["memory_id"] == "d3c27e63-9e2c-4c4a-8d7c-5c11a8c9f1aa"


def test_project_item_kind_inferred_from_id_prefix():
    out = _build_citations([{
        "index": 2,
        "id": "project_item:abc-123",
        "content": "Pinned quote about Cordoba",
    }])
    assert out[0]["kind"] == "project_item"
    assert out[0]["memory_id"] is None  # not a real memory row


def test_obsidian_kind_inferred_from_id_prefix():
    out = _build_citations([{
        "index": 3,
        "id": "obsidian:/Users/me/vault:notes/cordoba.md",
        "content": "Local note about the Emirate of Cordoba.",
    }])
    assert out[0]["kind"] == "obsidian"
    assert out[0]["memory_id"] is None


def test_loop_memory_does_not_produce_deep_link():
    """Deep Dive loop-synthesised memory ids (`loop:memory:N`) aren't
    real memory rows. The builder must NOT bind `memory_id`, so the
    popover's "Open in Memory" button stays hidden — otherwise the
    user clicks it and lands on nothing."""
    out = _build_citations([{
        "index": 1,
        "id": "loop:memory:1",
        "kind": "memory",
        "title": "Prior note",
        "snippet": "User reads about Al-Andalus frequently.",
    }])
    assert out[0]["kind"] == "memory"
    assert out[0]["memory_id"] is None


def test_web_hit_preserves_url_and_title():
    out = _build_citations([{
        "index": 4,
        "id": "loop:web:4",
        "kind": "web",
        "title": "Umayyad conquest of Hispania",
        "snippet": "Tariq ibn Ziyad crossed the strait in 711 CE.",
        "url": "https://example.com/conquest",
    }])
    c = out[0]
    assert c["kind"] == "web"
    assert c["url"] == "https://example.com/conquest"
    assert c["title"] == "Umayyad conquest of Hispania"
    assert c["memory_id"] is None


def test_empty_retrieved_yields_empty_citations():
    assert _build_citations([]) == []


def test_missing_id_entries_are_skipped():
    # A malformed entry with no id shouldn't break the whole turn —
    # skip it and keep going with the good ones.
    out = _build_citations([
        {"index": 1, "content": "no id here"},
        {"index": 2, "id": "real-id", "content": "valid"},
    ])
    assert len(out) == 1
    assert out[0]["idx"] == 2


def test_idx_falls_back_to_position_when_index_missing():
    out = _build_citations([
        {"id": "a", "content": "first"},
        {"id": "b", "content": "second"},
    ])
    assert [c["idx"] for c in out] == [1, 2]


def test_snippet_is_truncated_to_600_chars():
    long = "x" * 2000
    out = _build_citations([{"index": 1, "id": "a", "content": long}])
    assert len(out[0]["snippet"]) == 600


def test_title_falls_back_to_first_60_chars_when_content_short():
    out = _build_citations([{"index": 1, "id": "a", "content": "short"}])
    assert out[0]["title"] == "short"
