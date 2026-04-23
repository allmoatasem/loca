"""Unit tests for `src.mcp_server`.

Exercises the tool handlers directly without spinning up stdio.
Each handler should:
- route off the tool name
- return a single TextContent whose `text` field is valid JSON
- degrade cleanly on missing args / unknown tool / handler exceptions

We don't test the MCP SDK itself — just our surface.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.store as store_module  # noqa: E402
from src.mcp_server import (  # noqa: E402
    TOOLS,
    _handle_memory_add,
    _handle_memory_list,
    _handle_memory_recall,
    _handle_vault_search,
    build_server,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


def _parse(content_list) -> object:
    assert len(content_list) == 1
    assert content_list[0].type == "text"
    return json.loads(content_list[0].text)


# ---------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------

def test_tools_exposes_four_handlers():
    names = {t.name for t in TOOLS}
    assert names == {"memory_recall", "memory_list", "memory_add", "vault_search"}


def test_each_tool_has_description_and_schema():
    for tool in TOOLS:
        assert tool.description, f"{tool.name} missing description"
        assert tool.inputSchema.get("type") == "object"
        assert "properties" in tool.inputSchema


def test_build_server_wires_handlers():
    # Just a smoke check: construction succeeds and produces a Server.
    server = build_server()
    assert server is not None


# ---------------------------------------------------------------------
# memory_list
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_list_returns_empty_total_when_store_empty():
    out = _parse(await _handle_memory_list({}))
    assert out == {"total": 0, "offset": 0, "memories": []}


@pytest.mark.asyncio
async def test_memory_list_returns_stored_rows():
    store_module.add_memory("user prefers fastapi", type="user_fact")
    store_module.add_memory("paris rainy in april", type="knowledge")
    out = _parse(await _handle_memory_list({}))
    assert out["total"] == 2
    assert len(out["memories"]) == 2
    contents = [m["content"] for m in out["memories"]]
    assert "user prefers fastapi" in contents
    assert "paris rainy in april" in contents


@pytest.mark.asyncio
async def test_memory_list_filters_by_type():
    store_module.add_memory("a", type="user_fact")
    store_module.add_memory("b", type="knowledge")
    out = _parse(await _handle_memory_list({"type": "knowledge"}))
    assert out["total"] == 1
    assert out["memories"][0]["content"] == "b"


# ---------------------------------------------------------------------
# memory_add
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_add_persists_row():
    out = _parse(await _handle_memory_add({"content": "Deploys on Fridays."}))
    assert "id" in out and out["type"] == "user_fact"
    rows = store_module.list_memories(limit=5)
    assert len(rows) == 1
    assert rows[0]["content"] == "Deploys on Fridays."


@pytest.mark.asyncio
async def test_memory_add_rejects_empty_content():
    out = _parse(await _handle_memory_add({"content": "   "}))
    assert "error" in out
    assert store_module.count_memories() == 0


@pytest.mark.asyncio
async def test_memory_add_rejects_unknown_type():
    out = _parse(await _handle_memory_add({"content": "x", "type": "bogus"}))
    assert "error" in out
    assert store_module.count_memories() == 0


# ---------------------------------------------------------------------
# memory_recall (fallback keyword path — no plugin manager spin-up)
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_recall_empty_query_returns_empty():
    out = _parse(await _handle_memory_recall({"query": "   "}))
    assert out == []


@pytest.mark.asyncio
async def test_memory_recall_keyword_fallback():
    # Force the plugin-manager path to fail so we exercise the
    # keyword fallback without needing a real inference backend.
    store_module.add_memory("fastapi deploys to railway weekly", type="user_fact")
    store_module.add_memory("totally unrelated", type="knowledge")
    with patch("src.plugin_manager.PluginManager", side_effect=RuntimeError("no-op")):
        out = _parse(await _handle_memory_recall({"query": "fastapi", "limit": 5}))
    assert len(out) == 1
    assert "fastapi" in out[0]["content"].lower()


# ---------------------------------------------------------------------
# vault_search
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vault_search_empty_query_returns_empty():
    out = _parse(await _handle_vault_search({"query": ""}))
    assert out == []


@pytest.mark.asyncio
async def test_vault_search_uses_watcher_search():
    fake_hits = [{
        "title": "Cordoba",
        "rel_path": "history/cordoba.md",
        "vault_path": "/vault",
        "snippet": "Abd al-Rahman founded the Emirate of Cordoba.",
        "score": 0.87,
    }]
    with patch("src.obsidian_watcher.search_watched_vaults", return_value=fake_hits):
        out = _parse(await _handle_vault_search({"query": "cordoba", "limit": 5}))
    assert len(out) == 1
    assert out[0]["title"] == "Cordoba"
    assert out[0]["rel_path"] == "history/cordoba.md"
