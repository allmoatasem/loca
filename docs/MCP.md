# MCP integration

Loca ships an MCP server that exposes its memory store and Obsidian
Watcher vaults as tools. External MCP-aware clients (Claude Desktop,
Cursor, any MCP IDE) can call Loca to recall past conversations,
look up vault notes, and persist durable user facts — without going
through the OpenAI-tools passthrough on `/v1/chat/completions`.

## Exposed tools

| Tool             | Purpose                                                                   |
|------------------|---------------------------------------------------------------------------|
| `memory_recall`  | Semantic search over stored memories (query → top-K with scores).         |
| `memory_list`    | Paginated list of memories, optionally filtered by kind.                  |
| `memory_add`     | Store a new durable fact (`user_fact` / `knowledge` / `correction`).      |
| `vault_search`   | Semantic search across every registered Obsidian Watcher vault.           |

Destructive operations (delete memory, unregister vault) deliberately
stay out of the MCP surface — they live behind confirm prompts in the
Memory and Obsidian Watcher panels.

## Running it

The server speaks MCP over stdio:

```bash
python -m src.mcp_server
```

Clients launch that command themselves — you don't run it manually.

## Registering with Claude Desktop

Add Loca under `mcpServers` in your Claude Desktop config
(`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS):

```json
{
  "mcpServers": {
    "loca": {
      "command": "/path/to/Loca/.venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/Loca"
    }
  }
}
```

Restart Claude Desktop. The four tools appear under Loca's namespace
and are callable by the model.

## Registering with Cursor / other MCP clients

Same shape, same command — see your client's MCP docs for where its
config file lives. The entry is `{"command": "...", "args": [...]}`.

## When this is useful

- **Claude Desktop on the same Mac as Loca** — ask "what did I say
  about FastAPI last week?" and Claude routes it through `memory_recall`.
- **Cursor IDE** — pulls relevant vault notes into the editor context
  when you're writing code adjacent to topics the user has noted.
- **Any agentic client** — gets access to Loca's memory without
  having to re-implement the OpenAI-tools protocol.

This is a foundation: the tool surface will grow as we find patterns
that benefit from being MCP-callable rather than hidden inside
Loca's own chat.
