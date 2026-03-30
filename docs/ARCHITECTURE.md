# Loca — Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User interfaces                                 │
│                                                                         │
│   ┌──────────────────────────┐     ┌──────────────────────────────────┐ │
│   │  macOS native app        │     │  Browser (any platform)          │ │
│   │  (SwiftUI, Loca-SwiftUI/)│     │  http://localhost:8000           │ │
│   │  Hosts WKWebView →       │     │                                  │ │
│   │  http://localhost:8000   │     │                                  │ │
│   └────────────┬─────────────┘     └────────────────┬─────────────────┘ │
└────────────────┼──────────────────────────────────────┼──────────────────┘
                 │                                      │
                 └──────────────────┬───────────────────┘
                                    │ HTTP
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│  FastAPI proxy  src/proxy.py  :8000                                   │
│                                                                       │
│  • Serves the chat UI (src/static/index.html)                         │
│  • POST /v1/chat/completions  →  Orchestrator                         │
│  • GET/POST /api/conversations  →  SQLite store                       │
│  • GET/POST /api/memories  →  SQLite store                            │
│  • POST /api/extract-memories  →  memory extractor                   │
│  • GET /api/local-models  →  ModelManager.list_local()                │
│  • POST /api/models/load  →  ModelManager.load()                      │
│  • POST /api/models/download  →  ModelManager.download() + SSE       │
│  • POST /api/models/download/{id}/cancel|pause  →  task management   │
│  • DELETE /api/models/{name}  →  ModelManager.delete()                │
│  • GET /api/recommended-models  →  hardware_profiler (cached)         │
│  • GET /api/hardware  →  hardware_profiler.get_hardware_profile()     │
│  • POST /api/hardware/install-llmfit  →  auto-download llmfit binary  │
│  • GET /api/hf-search  →  Hugging Face Hub search API                 │
│  • GET /api/repo-files  →  Hugging Face sibling files for a repo      │
│  • POST /api/upload  →  image/pdf/text extraction                     │
└───────────┬────────────────────────────────────────────────────────── ┘
            │
            ▼
┌───────────────────────────────────────────────────────────────────────┐
│  Orchestrator  src/orchestrator.py                                    │
│                                                                       │
│  1. Route message → mode (general/code/reason)                        │
│  2. Load system prompt for that mode (prompts/*.md)                   │
│  3. Inject memories from store into system prompt                     │
│  4. Optionally trigger web search via SearXNG + trafilatura/Playwright│
│  5. Call inference backend (OpenAI-compatible /v1/chat/completions)   │
│  6. Parse tool calls in response, execute, re-call (max 5 rounds)     │
│  7. Stream or return final response                                   │
└───────────┬───────────────────────────────────────────────────────────┘
            │
            ▼
┌───────────────────────────────────────────────────────────────────────┐
│  InferenceBackend  src/inference_backend.py                           │
│                                                                       │
│  • Manages one inference subprocess at a time                         │
│  • Detects format: .gguf → llama-server, dir+config.json → mlx_lm    │
│  • Starts with correct flags, polls /health until ready               │
│  • api_base() → http://localhost:8080                                 │
└───────────┬───────────────────────────────────────────────────────────┘
            │  subprocess
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Inference server  :8080                                             │
│                                                                     │
│  macOS Apple Silicon + MLX model  →  mlx_lm.server                  │
│  Any platform + GGUF model        →  llama-server                   │
│                                                                     │
│  Both expose:  POST /v1/chat/completions  (OpenAI-compatible)        │
└─────────────────────────────────────────────────────────────────────┘

Separate process:
┌──────────────────────────────────────────────────────────────────────┐
│  SearXNG  :8888  (Python, own venv)                                  │
│  Used by web_search tool for web queries                             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component reference

| File | Purpose |
|---|---|
| `src/proxy.py` | FastAPI server. All HTTP endpoints live here. Wires together the other components. |
| `src/orchestrator.py` | Main request loop. Routing, search injection, memory injection, tool execution, streaming. |
| `src/inference_backend.py` | Subprocess manager for mlx_lm / llama-server. Start, stop, health-poll, restart. |
| `src/model_manager.py` | Local model inventory (list, load, delete) and HF Hub download with real-time SSE progress, pause/resume/cancel via HTTP Range headers. |
| `src/hardware_profiler.py` | Hardware detection and model recommendations. Runs llmfit (auto-downloaded); falls back to built-in catalog. Exposes `HardwareProfile` and `ModelRecommendation` dataclasses. |
| `src/router.py` | Keyword-based message routing to general/code/reason modes. Search trigger detection. |
| `src/store.py` | SQLite store (`data/loca.db`). Conversations (messages, title, starred, folder) and memories (user_fact, knowledge, correction). |
| `src/memory_extractor.py` | Three-pass LLM extraction of user facts, verified knowledge, and user corrections from conversations. |
| `src/tools/web_search.py` | SearXNG query + trafilatura or Playwright content extraction. |
| `src/tools/web_fetch.py` | Fetch and clean a single URL. |
| `src/tools/file_ops.py` | file_read / file_write tools. |
| `src/tools/shell.py` | Allowlisted shell_exec tool. |
| `src/static/index.html` | Single-file chat UI. All HTML, CSS, and JS. |
| `prompts/system_*.md` | System prompts per mode. Loaded fresh on every request. |
| `config.yaml` | All runtime configuration — inference backend, models dir, routing, search, tools, proxy. |
| `Loca-SwiftUI/` | Native macOS SwiftUI app. MVVM with `AppState` as the single source of truth, `BackendClient` for HTTP, and views in `Sources/Loca/Views/`. See [SWIFT_ARCHITECTURE.md](SWIFT_ARCHITECTURE.md). |
| `start_services.sh` | macOS startup: resets status file, checks deps, starts proxy + SearXNG. |
| `start_services_linux.sh` | Linux startup. |
| `start_services_windows.bat` | Windows startup. |
| `build_app.sh` | Builds and signs the macOS .app bundle. |

---

## Data flow — single message turn

```
User sends message
      │
      ▼
proxy.py  POST /v1/chat/completions
      │   reads: mode, model_override, num_ctx, research_mode, stream
      │
      ▼
orchestrator.handle()
      │
      ├─ router.route(message)  →  RouteResult(model, search_triggered, search_query)
      │
      ├─ model_manager.ensure_loaded()  →  (model_name, api_base)
      │
      ├─ load system prompt from prompts/system_{mode}.md
      │
      ├─ store.get_memories_context()  →  inject <memory> block into system prompt
      │
      ├─ [if search_triggered]
      │     web_search() via SearXNG → trafilatura or Playwright
      │     inject search results into last user message
      │
      ├─ call inference backend  POST {api_base}/v1/chat/completions
      │     payload: {model, messages, stream, num_ctx?}
      │
      ├─ [if response contains {"tool": "...", "args": {...}}]
      │     execute tool → inject result as user message → re-call (max 5x)
      │
      └─ stream or return final response
```

---

## Memory system

Three memory types, all stored in `data/loca.db`:

| Type | What it captures | Example |
|---|---|---|
| `user_fact` | Durable facts about the user — preferences, projects, hardware | "Uses M3 Ultra 96 GB" |
| `knowledge` | Facts verified via tool calls (web_search, web_fetch) in past conversations | "2026 Oscar Best Picture: One Battle After Another — source: web_search" |
| `correction` | Rules the user has taught the model | "Trust web_search results over training cutoff" |

Extraction runs after each conversation turn via `POST /api/extract-memories`. The injected `<memory>` block in the system prompt is grouped by type so the model sees corrections as explicit rules.

---

## Hardware profiler and model recommendations

`src/hardware_profiler.py` provides two public functions used by the proxy:

- `get_hardware_profile()` → `HardwareProfile` — detects RAM, CPU, Apple Silicon / NVIDIA GPU
- `get_recommendations(profile)` → `list[ModelRecommendation]` — returns models ranked for the machine

**llmfit path (primary):** llmfit is a small Go binary ([AlexsJones/llmfit](https://github.com/AlexsJones/llmfit)) that scores up to 1000 models per hardware profile. Loca auto-downloads it to `.llmfit/` on first use. The proxy caches results at startup using an `asyncio.Lock` so only one llmfit process ever runs at a time.

**Fallback path:** If llmfit is unavailable, a built-in catalog of ~8 curated Qwen2.5 / MLX models is used, filtered by `total_ram_gb`.

Recommendations are cached in `_recs_cache` (in-memory dict) and served from `/api/recommended-models`. A `?force=true` query parameter rebuilds the cache.

---

## Download flow

```
POST /api/models/download
      │  {repo_id, filename?, format}
      │
      ▼
asyncio.Queue + asyncio.Task per download_id
      │
      ▼
ModelManager.download(repo_id, filename, format)
      │
      ├─ MLX: fetch file list from HF API, download each .safetensors
      │       Range headers for resume; partial bytes pre-credited
      │
      └─ GGUF: single file, Range header for resume
      │
      yields DownloadProgress(percent, done, error)
      │
      ▼
GET /api/models/download/{id}/progress  →  SSE stream
POST /api/models/download/{id}/cancel   →  task.cancel() + delete partial files
POST /api/models/download/{id}/pause    →  task.cancel() (partial files kept for resume)
```

---

## Models directory layout

```
~/loca_models/
  gguf/
    qwen2.5-7b-q4_k_m.gguf        ← llama-server
    llama-3.3-70b-q4_k_m.gguf
  mlx/
    mlx-community--Llama-3.3-70B-Instruct-4bit/   ← mlx_lm.server (Apple Silicon)
      config.json
      model.safetensors
      tokenizer.json
      ...
```

Only one model is active at a time. Switching triggers an inference backend restart (~2–4 s).
