# Loca — Setup Guide

## macOS (native app + browser)

### Prerequisites

| Requirement | Install |
|---|---|
| Python 3.12 | `brew install python@3.12` |
| llama.cpp server | `brew install llama.cpp` |
| mlx_lm (optional, Apple Silicon only) | `pip install mlx_lm` |
| Git | Comes with Xcode Command Line Tools |
| Xcode (for native app build only) | App Store |

### First run

```bash
# 1. Clone
git clone https://github.com/allmoatasem/loca
cd loca/Loca

# 2. (Optional) Build the native macOS app
./build_app.sh
# Opens as ~/Applications/Loca.app — double-click to launch

# 3. Or run directly from terminal
./start_services.sh
# Then open http://localhost:8000 in your browser
```

On first run, `start_services.sh` will:
1. Check `llama-server` is on PATH — exits with install hint if not
2. Create a Python venv and install all dependencies from `requirements.txt`
3. Clone and set up SearXNG (one-time, takes ~2 min)
4. Start the FastAPI proxy on port 8000
5. Start SearXNG on port 8888

### Download your first model

Open Loca → **Manage Models** → **Discover** tab.

Loca automatically detects your hardware and shows recommended models sorted by fit score. Each card shows estimated size, tokens/sec, and whether the model is MLX (Apple Silicon) or GGUF. Click a card to start downloading.

For a specific model, use the **Search HF** tab or enter the repo ID directly:

- Quick start (small, GGUF): `bartowski/Qwen2.5-7B-Instruct-GGUF`, file: `Qwen2.5-7B-Instruct-Q4_K_M.gguf`
- Best quality (MLX, needs 36+ GB): `mlx-community/Qwen2.5-32B-Instruct-4bit`

See [MODELS.md](MODELS.md) for a full list.

### Updating

```bash
git pull origin main
./build_app.sh  # only needed if Swift files changed
# Python deps update automatically on next start_services.sh run
```

---

## Linux (browser only)

### Prerequisites

| Requirement | Install |
|---|---|
| Python 3.12+ | `sudo apt install python3.12 python3.12-venv` |
| llama.cpp server | Download from [github.com/ggerganov/llama.cpp/releases](https://github.com/ggerganov/llama.cpp/releases) — grab `llama-server` binary and put it on PATH |
| Git | `sudo apt install git` |

### First run

```bash
git clone https://github.com/allmoatasem/loca
cd loca/Loca
chmod +x start_services_linux.sh
./start_services_linux.sh
```

Open `http://localhost:8000` in your browser.

### Notes

- Only GGUF models work on Linux (MLX requires Apple Silicon)
- SearXNG is set up automatically on first run if Git is available
- To run as a background service, use `nohup ./start_services_linux.sh &` or a systemd unit

---

## Windows (browser only)

### Prerequisites

| Requirement | Install |
|---|---|
| Python 3.12 | [python.org/downloads](https://www.python.org/downloads) — check "Add to PATH" during install |
| llama.cpp server | Download `llama-server.exe` from [github.com/ggerganov/llama.cpp/releases](https://github.com/ggerganov/llama.cpp/releases) — place in a directory on your PATH |
| Git | [git-scm.com](https://git-scm.com) |

### First run

```bat
git clone https://github.com/allmoatasem/loca
cd loca\Loca
start_services_windows.bat
```

The script will open `http://localhost:8000` in your default browser automatically.

### Notes

- Only GGUF models work on Windows
- Web search (SearXNG) is not set up automatically on Windows — search will fall back to disabled. For full search support, install Docker and run SearXNG manually:
  ```
  docker run -d -p 8888:8080 searxng/searxng
  ```
  Then set `search.searxng_url: http://localhost:8888` in `config.yaml`

---

## Configuration reference

All settings live in `config.yaml`:

```yaml
inference:
  backend: auto            # auto | mlx | llama.cpp
  port: 8080               # inference server port
  models_dir: ~/loca_models  # where models are stored
  ctx_size: 32768          # default context window
  llama_server: llama-server  # binary name or full path
  active_model: null       # relative path to load on startup
                           # e.g. "gguf/qwen2.5-7b-q4_k_m.gguf"

routing:
  max_tool_calls_per_turn: 5

search:
  searxng_url: http://127.0.0.1:8888
  max_results: 5
  max_tokens_per_result: 500

proxy:
  host: 0.0.0.0
  port: 8000
```

### Setting a default model on startup

Edit `config.yaml` and set `inference.active_model` to a path relative to `models_dir`:

```yaml
inference:
  active_model: "gguf/qwen2.5-7b-q4_k_m.gguf"
```

Loca will load this model automatically on startup without needing to open the settings panel.
