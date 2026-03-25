# Local AI Orchestrator

A lightweight proxy that sits between **Open WebUI** and **LM Studio**, adding:
- Intelligent model routing (keyword/heuristic — zero extra LLM calls, no latency overhead)
- Automatic web search injection via SearXNG
- Tool use: web search, web fetch, file I/O, shell execution
- Specialist model load/unload tracking with idle timeout

---

## Architecture

```
Open WebUI → Proxy (localhost:8000) → LM Studio (localhost:1234)
                    ↓
              Router (heuristic)
              Web Search (SearXNG)
              Tool execution
```

The proxy intercepts every `/v1/chat/completions` request, routes it to the right model, optionally injects web search results, and handles any tool calls the model makes — then returns the final response in standard OpenAI format.

---

## File Structure

```
local-orchestrator/
├── config.yaml              # Model registry, SearXNG URL, tool settings
├── requirements.txt
├── src/
│   ├── router.py            # Keyword/heuristic routing + /command overrides
│   ├── model_manager.py     # LM Studio model loading, idle timeout tracking
│   ├── orchestrator.py      # Main loop: route → search → call model → tools
│   ├── proxy.py             # FastAPI server (intercepts /v1/chat/completions)
│   └── tools/
│       ├── web_search.py    # SearXNG integration + trafilatura extraction
│       ├── web_fetch.py     # Single URL fetch + content extraction
│       ├── file_ops.py      # file_read / file_write
│       └── shell.py         # shell_exec with allowlist + timeout
├── prompts/
│   ├── system_general.md    # System prompt for general model
│   ├── system_reason.md     # System prompt for reasoning model
│   ├── system_code.md       # System prompt for coding model
│   └── tool_definitions.json
└── tests/
    ├── test_router.py
    └── test_tools.py
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Find your LM Studio model names

Start the LM Studio server, then:

```bash
curl http://localhost:1234/v1/models | python3 -m json.tool
```

Copy the `id` value for each model and update `config.yaml`:

```yaml
models:
  general:
    lmstudio_name: "your-exact-model-id-here"
  reason:
    lmstudio_name: "your-exact-model-id-here"
  code:
    lmstudio_name: "your-exact-model-id-here"
```

### 3. Configure SearXNG URL

Update `config.yaml` with your EliteDesk's Tailscale IP:

```yaml
search:
  searxng_url: "http://<elitedesk-tailscale-ip>:8080"
```

To deploy SearXNG on the EliteDesk:
```bash
docker compose -f docker-compose.searxng.yaml up -d
# Verify:
curl "http://localhost:8080/search?q=test&format=json"
```

### 4. Start LM Studio server

LM Studio → Local Server → Start Server (default port 1234).

### 5. Start the proxy

```bash
uvicorn src.proxy:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Configure Open WebUI

In Open WebUI settings:
- Set API base URL to `http://localhost:8000`
- Select "OpenAI-compatible" mode
- The proxy's `/v1/models` endpoint will populate the model list automatically
- Set default model to `general`

---

## Model Routing

Routing is keyword + heuristic based — no LLM call, no added latency. First match wins.

| Priority | Trigger | Model |
|---|---|---|
| 1 | Image attached | `general` — only vision-capable model |
| 2 | `/code` prefix | `code` (Qwen3 Coder Next) |
| 2 | `/reason` prefix | `reason` (Nemotron 3 Nano) |
| 2 | `/write` prefix | `write` (Qwen3.5 27B Claude Opus distilled) |
| 2 | `/general` prefix | `general` (Qwen3.5 35B A3B) |
| 3 | Multi-file / architecture / large codebase signals | `code` |
| 4 | Planning / trade-offs / math / logic / step-by-step | `reason` |
| 5 | Draft / summarize / email / essay / rewrite / edit | `write` |
| 6 | Everything else | `general` |

### Manual overrides

```
/code    <message>   → force Qwen3 Coder Next
/reason  <message>   → force Nemotron 3 Nano
/write   <message>   → force Qwen3.5 27B Claude Opus distilled
/general <message>   → force Qwen3.5 35B A3B
/web     <query>     → trigger web search, feed results into current model
```

---

## Model Loading Strategy

- `general` stays loaded at all times.
- Only one specialist (`reason`, `code`, or `write`) is loaded alongside `general`.
- Before loading any specialist, any other currently loaded specialist is evicted from state first.
- After **10 minutes of inactivity**, the specialist is marked idle. LM Studio releases its memory when the next different model is called.

> **Note:** LM Studio doesn't expose an HTTP unload endpoint. The proxy tracks idle state and logs when a specialist should be released — the actual RAM swap happens when LM Studio loads the next requested model.

---

## Web Search

Auto-triggered when messages contain signals like:

- "latest", "current", "recently", "today"
- "news", "price", "stock", "release"
- "look up", "search for", "find information about"
- "who is the CEO/founder of...", version numbers, announcements

Force a search regardless: `/web <query>`

Results are injected as XML context before the model's system prompt:
```xml
<search_results>
  <result url="..." title="..." snippet="...">
    [extracted content, up to 500 tokens]
  </result>
  ...
</search_results>
```
The model is instructed to cite sources by URL.

---

## Tools Available to Models

Models can request tools by outputting a JSON call in their response:
```json
{"tool": "tool_name", "args": {"param": "value"}}
```

| Tool | Description |
|---|---|
| `web_search(query)` | Search via SearXNG, extract page content |
| `web_fetch(url)` | Fetch and extract readable text from a URL |
| `file_read(path)` | Read a local file (up to ~8k tokens) |
| `file_write(path, content, overwrite)` | Write or create a local file |
| `shell_exec(command)` | Run a whitelisted shell command with timeout |
| `image_describe(path, prompt)` | Vision analysis via the `general` model |

Max **5 tool calls per turn** (prevents runaway loops).

### Allowed shell commands

`ls`, `cat`, `grep`, `find`, `wc`, `head`, `tail`, `git`, `python3`, `pwd`

Configure in `config.yaml` under `tools.shell_exec.allowed_commands`.

---

## Config Reference

```yaml
models:
  general:
    lmstudio_name: "qwen3.5-35b-a3b"  # from `curl localhost:1234/v1/models`
    always_loaded: true
    idle_unload_minutes: null          # never idle-unload general
  reason:
    lmstudio_name: "nvidia/nemotron-3-nano"
    always_loaded: false
    idle_unload_minutes: 10
  code:
    lmstudio_name: "qwen/qwen3-coder-next"
    always_loaded: false
    idle_unload_minutes: 10
  write:
    lmstudio_name: "qwen3.5-27b-claude-4.6-opus-distilled-mlx"
    always_loaded: false
    idle_unload_minutes: 10

routing:
  default_model: general
  max_tool_calls_per_turn: 5

search:
  searxng_url: "http://<elitedesk-tailscale-ip>:8080"
  max_results: 5
  max_tokens_per_result: 500

tools:
  shell_exec:
    enabled: true
    timeout_seconds: 30
    allowed_commands: ["ls", "cat", "grep", "find", "wc", "head", "tail", "git", "python3", "pwd"]

proxy:
  host: "0.0.0.0"
  port: 8000
  lmstudio_base_url: "http://localhost:1234"
```

---

## Running Tests

```bash
# Offline tests only (no network or LM Studio required)
pytest tests/ -v -m "not network"

# All tests including network (requires SearXNG running)
pytest tests/ -v
```

---

## Verification Checklist

```bash
# Python version (needs 3.11+)
python3 --version

# Dependencies installed
python3 -c "import fastapi, httpx, trafilatura, pydantic, yaml; print('OK')"

# LM Studio server responding
curl http://localhost:1234/v1/models

# SearXNG reachable from Mac Studio
curl -s "http://<elitedesk-tailscale-ip>:8080/search?q=hello&format=json" | python3 -m json.tool | head -20

# Proxy running and routing
curl -s http://localhost:8000/v1/models | python3 -m json.tool
```
