# Loca — Local AI Orchestrator

A self-contained Mac app that runs a full local AI stack:

- **Open WebUI** — chat interface (localhost:3000)
- **LM Studio** — model backend (localhost:1234)
- **Orchestrator proxy** — sits between WebUI and LM Studio, adding intelligent routing, web search, and tool use (localhost:8000)
- **SearXNG** — local web search engine (localhost:8888)

Everything is set up automatically on first launch.

---

## Requirements

- macOS (Apple Silicon or Intel)
- [LM Studio](https://lmstudio.ai) installed
- Python 3.12 — `brew install python@3.12`
- Git (for cloning SearXNG on first run)

---

## Quick Start

1. Clone the repo
2. Open `Loca.app` (double-click, or `open Loca.app`)
3. On first launch it will set up everything automatically (~5 min)
4. Browser opens at `http://localhost:3000` when ready

> **Note:** The app bundle is not included in the repo. Build it by copying `Loca.app/Contents/MacOS/LocalAI` and creating the bundle structure, or just run `start.sh` directly from the terminal.

---

## Running from the terminal

```bash
cd /path/to/Loca
bash Loca.app/Contents/MacOS/LocalAI
```

Press `Ctrl+C` to stop everything cleanly.

---

## Configuration

Edit `config.yaml` before first launch to set your model names.

### Finding your LM Studio model IDs

Start the LM Studio server, then:

```bash
curl http://localhost:1234/v1/models | python3 -m json.tool
```

Copy the `id` values and update `config.yaml`:

```yaml
models:
  general:
    lmstudio_name: "your-model-id-here"
  reason:
    lmstudio_name: "your-model-id-here"
  code:
    lmstudio_name: "your-model-id-here"
  write:
    lmstudio_name: "your-model-id-here"
```

The proxy also auto-syncs model names from LM Studio on every launch via `src/model_sync.py`.

---

## Architecture

```
Open WebUI (3000) → Proxy (8000) → LM Studio (1234)
                         ↓
                    Router (heuristic)
                    Web Search (SearXNG :8888)
                    Tool execution
```

The proxy intercepts every `/v1/chat/completions` request, routes it to the right model, optionally injects web search results, and handles tool calls — returning the final response in standard OpenAI format.

---

## Model Routing

Routing is keyword/heuristic based — no LLM call, no added latency. First match wins.

| Priority | Trigger | Model |
|---|---|---|
| 1 | Model selected in UI dropdown | That model directly |
| 2 | Image attached | `general` — only vision-capable model |
| 3 | `/code` prefix | `code` |
| 3 | `/reason` prefix | `reason` |
| 3 | `/write` prefix | `write` |
| 3 | `/general` prefix | `general` |
| 4 | Multi-file / architecture / large codebase signals | `code` |
| 5 | Planning / trade-offs / math / logic / step-by-step | `reason` |
| 6 | Draft / summarize / email / essay / rewrite / edit | `write` |
| 7 | Everything else | `general` |

### Manual overrides (in message)

```
/code    <message>   → force code model
/reason  <message>   → force reasoning model
/write   <message>   → force writing model
/general <message>   → force general model
/web     <query>     → trigger web search into current model
```

---

## Web Search

Auto-triggered when messages contain signals like "latest", "current", "news", "price", "look up", "search for", etc.

Results are fetched from SearXNG and injected as context before the model responds. Force a search with `/web <query>`.

---

## Tools Available to Models

Models can call tools by outputting a JSON block in their response:
```json
{"tool": "tool_name", "args": {"param": "value"}}
```

| Tool | Description |
|---|---|
| `web_search(query)` | Search via SearXNG, extract page content |
| `web_fetch(url)` | Fetch and extract readable text from a URL |
| `file_read(path)` | Read a local file |
| `file_write(path, content)` | Write a local file |
| `shell_exec(command)` | Run a whitelisted shell command |
| `image_describe(path, prompt)` | Vision analysis via the general model |

Max 5 tool calls per turn. Allowed shell commands are configured in `config.yaml`.

---

## Logs

```
/tmp/loca-proxy.log      # Orchestrator proxy
/tmp/loca-webui.log      # Open WebUI
/tmp/loca-searxng.log    # SearXNG
```

---

## Running Tests

```bash
cd Loca
source .venv/bin/activate
pytest tests/ -v -m "not network"   # offline only
pytest tests/ -v                    # all (requires SearXNG running)
```
