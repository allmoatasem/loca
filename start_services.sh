#!/bin/bash

# Loca — macOS startup script
# Starts the Python proxy (which manages the inference backend directly).
# No LM Studio required.

# Resolve project DIR — prefer explicit path written by build_app.sh,
# fall back to relative path (works when running directly from the dev bundle).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/project_path.txt" ]; then
    DIR="$(cat "$SCRIPT_DIR/project_path.txt")"
else
    DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

VENV="$DIR/.venv"
VENV_SEARXNG="$DIR/.venv-searxng"
SEARXNG_SRC="$DIR/searxng"

# Find Python 3.12 — check PATH first, then common Homebrew locations
PYTHON312=$(command -v python3.12 2>/dev/null || true)
if [ -z "$PYTHON312" ]; then
    for p in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        [ -f "$p" ] && PYTHON312="$p" && break
    done
fi

notify() {
    osascript -e "display notification \"$1\" with title \"Loca\" sound name \"Funk\""
}

status() {
    local msg="$1"
    local progress="${2:-0}"
    printf '{"stage":"%s","progress":%d}\n' "$msg" "$progress" > /tmp/loca-startup-status.json
    notify "$msg"
}

shutdown() {
    notify "Shutting down Loca..."
    [ -f /tmp/loca-searxng.pid ] && kill "$(cat /tmp/loca-searxng.pid)" 2>/dev/null || true
    [ -f /tmp/loca-proxy.pid ]   && kill "$(cat /tmp/loca-proxy.pid)"   2>/dev/null || true
    exit 0
}

bail() {
    osascript -e "display alert \"Loca failed to start\" message \"$1\" as critical"
    shutdown
}

# ── 1. Check inference backend binaries ──────────────────────────────────────
# llama-server is required for GGUF models (cross-platform).
# mlx_lm is optional — only needed for MLX models on Apple Silicon.
if ! command -v llama-server > /dev/null 2>&1; then
    bail "llama-server not found. Install llama.cpp via Homebrew:
  brew install llama.cpp
Then relaunch Loca."
fi

# ── 2. Orchestrator venv ──────────────────────────────────────────────────────
_venv_ok=0
if [ -d "$VENV" ] \
   && "$VENV/bin/python" -c "import sys" 2>/dev/null \
   && "$VENV/bin/pip" --version > /dev/null 2>&1; then
    _venv_ok=1
fi

if [ "$_venv_ok" -eq 0 ]; then
    status "Setting up Python environment…" 10
    rm -rf "$VENV"
    python3 -m venv "$VENV" || bail "Failed to create Python venv."
    status "Installing dependencies…" 20
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install dependencies."
elif [ "$DIR/requirements.txt" -nt "$VENV/bin/pip" ]; then
    status "Installing new dependencies…" 20
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install dependencies."
fi

# ── 3. SearXNG setup (first-run) ─────────────────────────────────────────────
if [ -z "$PYTHON312" ] || [ ! -f "$PYTHON312" ]; then
    bail "Python 3.12 not found. Run: brew install python@3.12"
fi

if [ ! -d "$SEARXNG_SRC" ]; then
    status "Cloning SearXNG (first run)…" 30
    git clone --depth=1 https://github.com/searxng/searxng "$SEARXNG_SRC" 2>/dev/null \
        || bail "Failed to clone SearXNG. Check internet connection."
fi

if [ ! -d "$VENV_SEARXNG" ]; then
    status "Installing SearXNG (first run, ~2 min)…" 35
    "$PYTHON312" -m venv "$VENV_SEARXNG" || bail "Failed to create SearXNG venv."
    "$VENV_SEARXNG/bin/pip" install -U pip setuptools wheel pyyaml -q \
        || bail "Failed to upgrade pip for SearXNG."
    "$VENV_SEARXNG/bin/pip" install -r "$SEARXNG_SRC/requirements.txt" -q \
        || bail "Failed to install SearXNG requirements."
    "$VENV_SEARXNG/bin/pip" install --use-pep517 --no-build-isolation -e "$SEARXNG_SRC" -q \
        || bail "Failed to install SearXNG."
fi

# ── 3. Start orchestrator proxy ───────────────────────────────────────────────
status "Starting Loca…" 60
lsof -ti tcp:8000 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
cd "$DIR"
"$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
    > /tmp/loca-proxy.log 2>&1 &
echo $! > /tmp/loca-proxy.pid

for i in $(seq 1 30); do
    sleep 1
    curl -s http://localhost:8000/health > /dev/null 2>&1 && break
    [ "$i" -eq 30 ] && bail "Proxy didn't start. Check /tmp/loca-proxy.log"
done

# ── 4. Start SearXNG ─────────────────────────────────────────────────────────
status "Starting search…" 80
lsof -ti tcp:8888 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
cd "$SEARXNG_SRC"
SEARXNG_SETTINGS_PATH="$DIR/searxng-settings.yml" \
"$VENV_SEARXNG/bin/python" searx/webapp.py \
    > /tmp/loca-searxng.log 2>&1 &
echo $! > /tmp/loca-searxng.pid
cd "$DIR"

for i in $(seq 1 30); do
    sleep 2
    curl -s http://127.0.0.1:8888/ > /dev/null 2>&1 && break
    [ "$i" -eq 30 ] && bail "SearXNG didn't start. Check /tmp/loca-searxng.log"
done

# ── 5. Signal ready ──────────────────────────────────────────────────────────
status "Ready" 100

# ── Keep alive + watchdog ─────────────────────────────────────────────────────
trap shutdown INT TERM EXIT

while true; do
    sleep 15

    # Proxy watchdog
    if ! kill -0 "$(cat /tmp/loca-proxy.pid 2>/dev/null)" 2>/dev/null; then
        cd "$DIR"
        "$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
            >> /tmp/loca-proxy.log 2>&1 &
        echo $! > /tmp/loca-proxy.pid
    fi

    # SearXNG watchdog
    if ! kill -0 "$(cat /tmp/loca-searxng.pid 2>/dev/null)" 2>/dev/null; then
        cd "$SEARXNG_SRC"
        SEARXNG_SETTINGS_PATH="$DIR/searxng-settings.yml" \
        "$VENV_SEARXNG/bin/python" searx/webapp.py \
            >> /tmp/loca-searxng.log 2>&1 &
        echo $! > /tmp/loca-searxng.pid
        cd "$DIR"
    fi
done
