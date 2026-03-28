# Loca — Local AI Chat

A native macOS app that wraps LM Studio with a custom FastAPI orchestration proxy, SearXNG web search, and a Swift WKWebView frontend. Loca routes each message to the right local model, optionally injects live web results, remembers facts about you across sessions, and renders responses with full syntax highlighting — all running entirely on your machine, no cloud required.

---

## What is Loca

Loca is a self-contained local AI stack packaged as a macOS `.app` bundle. A Swift shell hosts a `WKWebView` that talks to a FastAPI proxy running on `localhost:8000`. The proxy intercepts every chat request, applies keyword-based model routing, injects memory context and web search results when relevant, and forwards the request to LM Studio's OpenAI-compatible API at `localhost:1234`. A local SearXNG instance (`localhost:8888`) handles web search; when SearXNG returns no results, a headless Chromium browser (Playwright) falls back to DuckDuckGo automatically.

All conversation history and extracted user memories are persisted in a local SQLite database (`data/loca.db`). Nothing leaves your machine.

---

## Requirements

| Requirement | Notes |
|---|---|
| macOS (Apple Silicon or Intel) | Tested on macOS 14+ |
| [LM Studio](https://lmstudio.ai) | Must be running with server enabled on port 1234 |
| Python 3.12 | `brew install python@3.12` |
| Git | For cloning SearXNG on first run |
| Playwright + Chromium | `pip install playwright && playwright install chromium` |

---

## Quick Start

```bash
# 1. Clone the repo
git clone <repo-url> Loca
cd Loca

# 2. Build the .app bundle (signs it and syncs to ~/Applications/)
bash build_app.sh

# 3. Launch
open ~/Applications/Loca.app
```

On first launch the app starts the Python proxy, SearXNG, and opens the chat UI automatically. LM Studio must already be running with its local server enabled.

To run without building the app:

```bash
bash start_services.sh
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Loca.app (Swift)                  │
│              WKWebView  ←→  localhost:8000          │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP
┌─────────────────────▼───────────────────────────────┐
│            Orchestrator Proxy  :8000                │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │  Router  │  │  Memory  │  │  Tool dispatcher  │ │
│  └──────────┘  └──────────┘  └───────────────────┘ │
│  ┌─────────────────────────────────────────────┐   │
│  │          Conversation store (SQLite)         │   │
│  └─────────────────────────────────────────────┘   │
└──────┬──────────────────────┬──────────────────────┘
       │ OpenAI API           │ HTTP
┌──────▼───────┐    ┌─────────▼──────┐
│  LM Studio   │    │    SearXNG     │
│    :1234     │    │     :8888      │
└──────────────┘    └────────────────┘
                           │
                 ┌─────────▼──────┐
                 │   Playwright   │
                 │ (fallback/     │
                 │  research)     │
                 └────────────────┘
```

**Key source files:**

| File | Role |
|---|---|
| `LocalAI.swift` | Swift entry point — WKWebView window, titlebar drag, logging |
| `src/proxy.py` | FastAPI app — all HTTP routes, SSE streaming |
| `src/orchestrator.py` | Coordinates routing, memory, tools, and LM Studio calls |
| `src/router.py` | Keyword/heuristic model routing (no LLM call) |
| `src/store.py` | SQLite persistence for conversations and memories |
| `src/memory_extractor.py` | LLM-based extraction of durable user facts |
| `src/tools/web_search.py` | SearXNG search + content extraction |
| `src/tools/playwright_fetch.py` | Headless browser fetch and DuckDuckGo fallback |
| `src/static/index.html` | Full chat UI (vanilla JS, no framework) |

---

## Configuration

Edit `config.yaml` before first launch.

```yaml
models:
  general:
    lmstudio_name: "your-model-id"   # vision-capable; used as default
    always_loaded: true
  reason:
    lmstudio_name: "your-reasoning-model-id"
    idle_unload_minutes: 10
  code:
    lmstudio_name: "your-code-model-id"
    idle_unload_minutes: 10
  write:
    lmstudio_name: "your-writing-model-id"
    idle_unload_minutes: 10

routing:
  default_model: general
  max_tool_calls_per_turn: 5

search:
  searxng_url: http://127.0.0.1:8888
  max_results: 5
  max_tokens_per_result: 500

tools:
  shell_exec:
    enabled: true
    timeout_seconds: 30
    allowed_commands: [ls, cat, grep, find, wc, head, tail, git, python3, pwd]

proxy:
  host: 0.0.0.0
  port: 8000
  lmstudio_base_url: http://localhost:1234
```

To find your LM Studio model IDs:

```bash
curl http://localhost:1234/v1/models | python3 -m json.tool
```

---

## Model Routing

Routing is keyword/heuristic-based — no LLM call, no added latency. First match wins.

| Priority | Trigger | Model |
|---|---|---|
| 1 | `/web <query>` prefix | Current model + web search forced |
| 2 | Model hint from client | That model directly |
| 3 | Image attached | `general` (only vision-capable model) |
| 4 | `/code` prefix | `code` |
| 4 | `/reason` prefix | `reason` |
| 4 | `/write` prefix | `write` |
| 4 | `/general` prefix | `general` |
| 5 | Multi-file / architecture / large-codebase signals | `code` |
| 6 | Planning / trade-offs / math / logic / step-by-step | `reason` |
| 7 | Draft / summarize / email / essay / rewrite / edit | `write` |
| 8 | Everything else | `general` |

### Slash commands

```
/code    <message>   → force code model
/reason  <message>   → force reasoning model
/write   <message>   → force writing model
/general <message>   → force general model
/web     <query>     → force web search into current model
```

---

## Conversation History

Every turn is automatically saved to `data/loca.db`. The sidebar lists all past conversations ordered by last update. Click any entry to reload it; click the trash icon to delete it permanently. A new conversation is started automatically when you click "New Chat" or open the app fresh.

Conversations store: ID, title (first user message, truncated), timestamp, model used, and the full message list as JSON.

---

## Memory System

After each conversation, Loca can extract durable facts about you (preferences, context, names, ongoing projects) and store them as individual memory entries in SQLite.

**How it works:**

1. When a new conversation starts, existing memories are fetched and injected into the system prompt inside a `<memory>` XML block.
2. After a conversation ends (or on demand), the orchestrator calls the local LLM with a structured extraction prompt to identify new facts worth remembering.
3. Extracted facts are deduplicated and stored as discrete memory entries.

**Memory panel** (click the brain icon in the UI):

- View all stored memories
- Add a memory manually
- Delete individual memories
- Trigger extraction from the current conversation

Memory entries are capped at 15 for context injection to avoid bloating the system prompt.

---

## Web Search and Research Mode

### Auto-triggered keywords

Web search fires automatically when the message contains signals like:

`latest`, `current`, `today`, `news`, `price`, `stock`, `tickets`, `showtimes`, `cinema`, `near me`, `opening hours`, `booking`, `find`, `look up`, `search for`, `how much`, `what time`, `where can`, and more.

### SearXNG + Playwright fallback

1. Query goes to SearXNG (local Docker container on port 8888).
2. Top results are fetched and content extracted with `trafilatura`.
3. If SearXNG returns zero results, Playwright opens DuckDuckGo in a headless Chromium browser, scrapes the SERP, and fetches the top pages directly.

### Research mode

Click the **Research** button next to Send to enable research mode for the current message. In research mode:

- SearXNG and Playwright run in parallel.
- All result URLs are fetched via headless browser (full JS-rendered content).
- More result pages are fetched (not just top 5).

Extracted content is injected as context before the model responds.

---

## Tools Available

Models can invoke tools by outputting a JSON block in their response. The orchestrator intercepts the block, executes the tool, and feeds results back — up to 5 tool calls per turn.

| Tool | Description |
|---|---|
| `web_search(query)` | Search via SearXNG (+ Playwright fallback), extract page content |
| `web_fetch(url)` | Fetch readable text from a URL |
| `file_read(path)` | Read a local file |
| `file_write(path, content)` | Write a local file |
| `shell_exec(command)` | Run a whitelisted shell command (see `config.yaml`) |
| `image_describe(path, prompt)` | Vision analysis via the general model |

---

## Chat UI Features

- **Conversation sidebar** — full history list; click to load, delete with trash icon
- **Memory panel** — slide-in view of all memories; add, delete, or trigger extraction
- **Research mode** — toggle button next to Send for deep parallel search
- **Syntax highlighting** — Prism.js with 16 language bundles (Python, JS, TS, Rust, Go, C, C++, Java, Swift, SQL, YAML, JSON, CSS, HTML, Bash); dark/light theme syncs with OS
- **Live code blocks in input** — type `` ```python `` and press Enter to create a live-highlighted editable code block in the input field
- **Live markdown in input** — `**bold**`, `*italic*`, `` `code` `` rendered as you type
- **Compose while streaming** — input stays fully editable while the assistant is responding
- **Full text selection** — all bubbles, stats bar, and RAM usage are selectable
- **Copy buttons** — on both user and assistant message bubbles
- **Accurate tok/s** — calculated over the full round-trip time, not chunk-delivery intervals
- **Actual model name** — stats bar shows the real LM Studio model name, not an alias

---

## Building the App

```bash
bash build_app.sh
```

This script:
1. Compiles `LocalAI.swift` into the `.app` bundle
2. Signs the full bundle with `codesign --deep`
3. Reads `project_path.txt` (auto-created with the repo path) and syncs the built app to `~/Applications/Loca.app`

The app references this repo at the path stored in `project_path.txt`, so services start from the correct location regardless of whether the `.app` was launched from `~/Applications/` or the repo directory.

---

## Logs

| File | Contents |
|---|---|
| `/tmp/loca-swift.log` | Swift app startup, WKWebView events, crash context |
| `/tmp/loca-proxy.log` | FastAPI proxy, routing decisions, tool calls |
| `/tmp/loca-searxng.log` | SearXNG Docker container output |

---

## Running Tests

```bash
cd /path/to/Loca
source .venv/bin/activate

pytest tests/ -v -m "not network"   # offline tests only
pytest tests/ -v                    # all tests (requires SearXNG running)
```
