# Loca — Local AI Chat

A local AI chat interface that runs entirely on your machine. No cloud, no subscriptions, no LM Studio required.

Loca manages its own inference backend (MLX on Apple Silicon, llama.cpp everywhere else), routes each message to the right system prompt mode, injects web search results and memory context, and runs as a native macOS app or in any browser on Linux and Windows.

---

## Features

- **No dependencies on LM Studio or Ollama** — drives `mlx_lm.server` and `llama-server` directly
- **MLX + GGUF models** — use MLX directories for Apple Silicon speed or GGUF files for cross-platform
- **Hardware-aware model discovery** — llmfit analyses your RAM/GPU and recommends the best MLX and GGUF models for your machine, with live fit scores, tokens/sec estimates, and format filters
- **In-app model management** — download from Hugging Face with real-time progress, pause/resume/cancel, switch models, delete, set context window
- **Persistent memory** — three types: user facts, verified knowledge, and corrections; injected into every conversation
- **Web search** — SearXNG + trafilatura, with optional Playwright deep research mode for dynamic sites
- **Tool use** — web_search, web_fetch, file_read, file_write, shell_exec, image_describe
- **Conversation history** — SQLite-backed, searchable, with folders and starring
- **Native macOS app** — SwiftUI shell with WKWebView; also works in any browser
- **Cross-platform** — macOS, Linux, Windows (browser interface)

---

## Requirements

### macOS
| Requirement | Install |
|---|---|
| Python 3.12 | `brew install python@3.12` |
| llama.cpp server | `brew install llama.cpp` |
| mlx_lm (optional, Apple Silicon only) | `pip install mlx_lm` |
| Xcode (native app only) | App Store |

### Linux
| Requirement | Install |
|---|---|
| Python 3.12+ | `sudo apt install python3.12 python3.12-venv` |
| llama-server | [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) |

### Windows
| Requirement | Install |
|---|---|
| Python 3.12 | [python.org](https://www.python.org/downloads) |
| llama-server.exe | [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) |

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
