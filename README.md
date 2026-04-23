# Loca — Local AI Chat

A local AI chat interface that runs entirely on your machine. No cloud, no subscriptions, no LM Studio required.

Loca manages its own inference backend (MLX on Apple Silicon, llama.cpp everywhere else), routes each message to the right system prompt mode, injects web search results and memory context, and runs as a native macOS app or in any browser on Linux and Windows.

---

## Features

### Inference
- **Native backend** — drives `mlx_lm.server` (Apple Silicon) and `llama-server` (all platforms) directly; no LM Studio or Ollama required
- **External server toggle** — switch to LM Studio, Ollama, or any OpenAI-compatible server from Preferences → Inference
- **MLX + GGUF** — MLX directories for native Apple Silicon speed; GGUF for cross-platform compatibility
- **Vision** — image uploads with multi-modal support; auto-detects vision capability from `config.json` and GGUF mmproj files
- **Voice mode** — speech-to-text via mlx-whisper with voice activity detection; transcribes and sends automatically. Available in both the SwiftUI native app and the Svelte browser UI, with matching VAD thresholds and hands-free auto-resume after each response

### Model management
- **Hardware-aware discovery** — llmfit analyses your RAM/GPU and ranks models by fit score, with tokens/sec estimates, format filters, and 491+ models paginated
- **In-app downloads** — fetch from Hugging Face with real-time progress, pause/resume/cancel, and disk-space guard
- **Performance tuning** — hardware-aware suggestions for GPU layers, batch size, and CPU threads

### Intelligence
- **Persistent memory** — three types (user facts, verified knowledge, corrections) injected into every conversation; gated auto-extraction + bulk-delete-by-kind keep the store tight
- **Research Partner** — scoped projects bundling sources, notes, background watches, and a one-click "Use Obsidian Watcher" toggle that draws live from the watched-vault index without re-ingestion
- **Autonomous research loop** — Researcher → Reviewer → Writer → Verifier pipeline with `[memory: N]` citations and a phantom-citation safety net (Deep Dive mode)
- **Obsidian Watcher** — app-level background sync of registered vaults: incremental re-index, per-vault scan-now / remove, always-live analyser with stats, orphans, broken links, and semantic search
- **Web search** — SearXNG + trafilatura; optional Playwright deep research mode for dynamic sites
- **Tool use** — `web_search`, `web_fetch`, `file_read`, `file_write`, `shell_exec`, `image_describe`
- **MCP server** — Loca exposes its memory + Obsidian vaults as MCP tools (`memory_recall`, `memory_list`, `memory_add`, `vault_search`) for Claude Desktop / Cursor / any MCP-aware client. See [docs/MCP.md](docs/MCP.md)
- **Per-conversation adapter override** — pin a LoRA to a single conversation; siblings in the same project still use the project default
- **Typo and intent handling** — system prompt understands informal/shorthand input

### App
- **Conversation history** — SQLite-backed, searchable, with folders and starring
- **App preferences** — theme, context window, inference recipes, system prompt override, typewriter stream + reading-speed slider
- **Clickable `[memory: N]` citations** — pills in the chat bubble open the Memory panel
- **System RAM indicator** — live sidebar footer chip matches Activity Monitor's "Memory Used" figure
- **Lockdown mode** — disable web search and Playwright with one toggle
- **Remote server** — connect to a Loca backend on a remote machine (e.g. via Tailscale); configurable from Preferences → Server
- **Native macOS app** — SwiftUI shell with WKWebView; also runs in any browser on Linux and Windows
- **DMG packaging** — `./build_app.sh --dmg` outputs a ready-to-distribute disk image
- **Glossary + Philosophy** — in-app reference pages for AI/ML terms and Loca's design principles

---

## Requirements

### macOS
| Requirement | Install |
|---|---|
| Python 3.12 | `brew install python@3.12` |
| llama.cpp server | `brew install llama.cpp` |
| mlx_lm (optional, Apple Silicon only) | `pip install mlx_lm` |
| espeak-ng (voice mode) | auto-installed by `start_services.sh`; `brew install espeak-ng` to do it manually |
| Xcode (native app only) | App Store |

### Linux
| Requirement | Install |
|---|---|
| Python 3.12+ | `sudo apt install python3.12 python3.12-venv` |
| llama-server | [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) |
| espeak-ng (voice mode) | `sudo apt install espeak-ng` |

### Windows
| Requirement | Install |
|---|---|
| Python 3.12 | [python.org](https://www.python.org/downloads) |
| llama-server.exe | [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) |
| espeak-ng (voice mode) | [espeak-ng releases](https://github.com/espeak-ng/espeak-ng/releases) |

---

## Quick start

### macOS

```bash
# Clone
git clone https://github.com/allmoatasem/loca
cd loca/Loca

# Option A: Native app
./build_app.sh        # builds ~/Applications/Loca.app
open ~/Applications/Loca.app

# Option B: Browser
./start_services.sh   # then open http://localhost:8000
```

### Linux

```bash
git clone https://github.com/allmoatasem/loca
cd loca/Loca
chmod +x start_services_linux.sh
./start_services_linux.sh
# Open http://localhost:8000
```

### Windows

```bat
git clone https://github.com/allmoatasem/loca
cd loca\Loca
start_services_windows.bat
```

On first run, Python dependencies and SearXNG are installed automatically. The only thing you need to install manually is `llama-server` (and `mlx_lm` on Mac if you want MLX models).

---

## Your first model

Open Loca → **Manage Models** → **Discover** tab.

Loca uses [llmfit](https://github.com/AlexsJones/llmfit) to analyse your hardware and automatically show the best models for your machine — sorted by fit score, with MLX and GGUF clearly labelled. Tap any model card to download it.

**Manual download** (Search HF tab or direct entry):
- GGUF (cross-platform): `bartowski/Qwen2.5-7B-Instruct-GGUF`, file: `Qwen2.5-7B-Instruct-Q4_K_M.gguf`
- MLX (Apple Silicon only): `mlx-community/Qwen2.5-32B-Instruct-4bit`, file: (leave blank)

See [docs/MODELS.md](docs/MODELS.md) for a full list of recommended models.

---

## Configuration

All settings in `config.yaml`. Key options:

```yaml
inference:
  models_dir: ~/loca_models   # where models are stored
  ctx_size: 32768             # default context window
  active_model: null          # set to auto-load on startup
                              # e.g. "gguf/qwen2.5-7b-q4_k_m.gguf"

search:
  searxng_url: http://127.0.0.1:8888

proxy:
  port: 8000
```

Full reference: [docs/SETUP.md](docs/SETUP.md)

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — system diagram, component reference, data flow
- [Models guide](docs/MODELS.md) — Discover tab, MLX vs GGUF, recommended models, quantisation guide
- [Setup guide](docs/SETUP.md) — per-platform install, config reference
- [Swift architecture](docs/SWIFT_ARCHITECTURE.md) — macOS app structure, MVVM pattern, AppState

---

## Development

```bash
# Install dev dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest pytest-asyncio pytest-cov

# Run tests
pytest tests/ -v

# Run the proxy in dev mode (hot reload)
uvicorn src.proxy:app --host 0.0.0.0 --port 8000 --reload
```

CI runs on every pull request: lint (ruff + mypy) and tests (pytest + coverage). Swift build runs on merges to main. See [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
