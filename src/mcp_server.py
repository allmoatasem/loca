"""Loca as an MCP server.

Exposes Loca's memory + Obsidian-vault stores as tools over stdio so
external MCP clients (Claude Desktop, Cursor, any MCP-aware IDE) can
call into Loca's knowledge layer without going through the
OpenAI-tools passthrough on `/v1/chat/completions`.

Surface (kept deliberately small — a foundation, not the whole API):

- `memory_recall(query, limit=8)` → semantic search over stored memories.
- `memory_list(type?, limit=50, offset=0)` → paginated list, newest first.
- `memory_add(content, type="user_fact")` → store a verbatim fact.
- `vault_search(query, limit=10)` → semantic search across every
  registered Obsidian Watcher vault.

The server is read-write for memories by design: if an external agent
learns a durable fact about the user, Loca's memory should be the
system of record. Destructive ops (delete) deliberately stay out —
those live behind the Memory panel's confirm prompts.

Run as::

    python -m src.mcp_server

which speaks the MCP protocol over stdio. Clients register Loca by
pointing at that command in their MCP config.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Tool definitions — kept as data so `list_tools` can return them
# unchanged and `call_tool` can route off the name field.
# ---------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="memory_recall",
        description=(
            "Semantic search over Loca's memory store. Returns the "
            "top-K memories most relevant to the query, ordered by "
            "score. Use this instead of memory_list when you have a "
            "specific topic in mind."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 8,
                    "description": "Max results (default 8, cap 50).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="memory_list",
        description=(
            "Paginated list of memories ordered newest-first. Filter "
            "by `type` (user_fact / knowledge / correction) when you "
            "need a specific kind; omit for mixed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["user_fact", "knowledge", "correction"],
                    "description": "Optional filter by memory kind.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
            },
        },
    ),
    Tool(
        name="memory_add",
        description=(
            "Store a new memory verbatim. Use this when you learn a "
            "durable fact about the user that should persist across "
            "future conversations — preferences, decisions, project "
            "context. Keep content short and declarative."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember, ideally one sentence.",
                },
                "type": {
                    "type": "string",
                    "enum": ["user_fact", "knowledge", "correction"],
                    "default": "user_fact",
                },
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="vault_search",
        description=(
            "Search every registered Obsidian Watcher vault for notes "
            "matching the query. Returns title, path, and a snippet "
            "per hit. Use this when the user's question is likely "
            "answered by their Obsidian notes rather than general "
            "chat history."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
]


# ---------------------------------------------------------------------
# Handlers — thin wrappers around the existing store + plugin layer.
# ---------------------------------------------------------------------

def _truncate(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _handle_memory_recall(args: dict[str, Any]) -> list[TextContent]:
    query = (args.get("query") or "").strip()
    if not query:
        return [TextContent(type="text", text="[]")]
    limit = int(args.get("limit") or 8)
    # Prefer the memory plugin (MemPalace) for semantic recall when
    # it's available; fall back to the built-in keyword scan in
    # store.list_memories otherwise.
    try:
        from .inference_backend import InferenceBackend  # noqa: PLC0415
        from .plugin_manager import PluginManager  # noqa: PLC0415
        from .proxy import _load_config  # noqa: PLC0415
        cfg = _load_config()
        backend = InferenceBackend(cfg)
        mgr = PluginManager(cfg, backend)
        await mgr.start()
        try:
            hits = await mgr.memory_plugin.recall(query, limit=limit)
        finally:
            await mgr.stop()
            await backend.stop()
    except Exception as exc:
        logger.warning("memory_recall fell back to keyword scan: %s", exc)
        hits = [
            m for m in store.list_memories(limit=200)
            if query.lower() in (m.get("content") or "").lower()
        ][:limit]
    summary = [
        {
            "id": str(h.get("id") or ""),
            "type": h.get("type") or "memory",
            "content": _truncate(str(h.get("content") or "")),
            "score": h.get("score"),
        }
        for h in hits
    ]
    return [TextContent(type="text", text=json.dumps(summary, indent=2))]


async def _handle_memory_list(args: dict[str, Any]) -> list[TextContent]:
    kind = args.get("type")
    limit = min(int(args.get("limit") or 50), 200)
    offset = max(int(args.get("offset") or 0), 0)
    rows = store.list_memories(limit=limit, type=kind, offset=offset)
    total = store.count_memories(type=kind)
    out = {
        "total": total,
        "offset": offset,
        "memories": [
            {
                "id": str(r.get("id") or ""),
                "type": r.get("type"),
                "content": _truncate(str(r.get("content") or "")),
                "created": r.get("created"),
            }
            for r in rows
        ],
    }
    return [TextContent(type="text", text=json.dumps(out, indent=2))]


async def _handle_memory_add(args: dict[str, Any]) -> list[TextContent]:
    content = (args.get("content") or "").strip()
    if not content:
        return [TextContent(type="text", text='{"error":"content is required"}')]
    kind = args.get("type") or "user_fact"
    if kind not in store.MEMORY_TYPES:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"type must be one of {sorted(store.MEMORY_TYPES)}"}),
        )]
    mid = store.add_memory(content=content, type=kind)
    return [TextContent(type="text", text=json.dumps({"id": mid, "type": kind}))]


async def _handle_vault_search(args: dict[str, Any]) -> list[TextContent]:
    query = (args.get("query") or "").strip()
    if not query:
        return [TextContent(type="text", text="[]")]
    limit = int(args.get("limit") or 10)
    from .obsidian_watcher import search_watched_vaults  # noqa: PLC0415
    hits = search_watched_vaults(query, limit=limit)
    summary = [
        {
            "title": h.get("title"),
            "rel_path": h.get("rel_path"),
            "vault_path": h.get("vault_path"),
            "snippet": _truncate(str(h.get("snippet") or "")),
            "score": h.get("score"),
        }
        for h in hits
    ]
    return [TextContent(type="text", text=json.dumps(summary, indent=2))]


_HANDLERS = {
    "memory_recall": _handle_memory_recall,
    "memory_list":   _handle_memory_list,
    "memory_add":    _handle_memory_add,
    "vault_search":  _handle_vault_search,
}


# ---------------------------------------------------------------------
# Server wiring
# ---------------------------------------------------------------------

def build_server() -> Server:
    """Construct the MCP Server with tool handlers wired up. Split
    out of `main()` so tests can instantiate + introspect without
    spinning up stdio."""
    server: Server = Server("loca")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handler = _HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
        try:
            return await handler(arguments or {})
        except Exception as exc:
            logger.exception("MCP tool %s failed", name)
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    return server


async def _run() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    """Entry point for `python -m src.mcp_server`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
