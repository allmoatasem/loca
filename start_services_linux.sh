#!/bin/bash

# Loca — Linux startup script
# Starts the Python proxy. Access Loca at http://localhost:8000 in your browser.
# Prerequisites: python3.12, llama-server (or mlx_lm for Apple Silicon)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$SCRIPT_DIR"
VENV="$DIR/.venv"
SEARXNG_SRC="$DIR/searxng"
VENV_SEARXNG="$DIR/.venv-searxng"

log() { echo "[Loca] $*"; }
bail() { echo "[Loca ERROR] $*" >&2; exit 1; }

# ── 1. venv setup ─────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ] || ! "$VENV/bin/python" -c "import sys" 2>/dev/null; then
    log "Setting up Python environment..."
    python3 -m venv "$VENV" || bail "Failed to create venv. Install python3-venv."
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install dependencies."
elif [ "$DIR/requirements.txt" -nt "$VENV/bin/pip" ]; then
    log "Installing new dependencies..."
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q
fi

# ── 2. Check llama-server is available ────────────────────────────────────────
if ! command -v llama-server > /dev/null 2>&1; then
    bail "llama-server not found. Install llama.cpp: https://github.com/ggerganov/llama.cpp/releases"
fi

# ── 3. SearXNG setup (optional, first-run) ────────────────────────────────────
SEARXNG_AVAILABLE=0
if [ ! -d "$SEARXNG_SRC" ]; then
    log "Cloning SearXNG (first run)..."
    git clone --depth=1 https://github.com/searxng/searxng "$SEARXNG_SRC" 2>/dev/null && SEARXNG_CLONED=1
fi

if [ -d "$SEARXNG_SRC" ] && [ ! -d "$VENV_SEARXNG" ]; then
    log "Installing SearXNG (first run, ~2 min)..."
    python3 -m venv "$VENV_SEARXNG"
    "$VENV_SEARXNG/bin/pip" install -U pip setuptools wheel pyyaml -q
    "$VENV_SEARXNG/bin/pip" install -r "$SEARXNG_SRC/requirements.txt" -q
    "$VENV_SEARXNG/bin/pip" install --use-pep517 --no-build-isolation -e "$SEARXNG_SRC" -q
fi

# ── 4. Start proxy ────────────────────────────────────────────────────────────
log "Starting Loca proxy..."
fuser -k 8000/tcp 2>/dev/null || true
cd "$DIR"
"$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
    > /tmp/loca-proxy.log 2>&1 &
PROXY_PID=$!
echo $PROXY_PID > /tmp/loca-proxy.pid

for i in $(seq 1 30); do
    sleep 1
    curl -s http://localhost:8000/health > /dev/null 2>&1 && break
    [ "$i" -eq 30 ] && bail "Proxy didn't start. Check /tmp/loca-proxy.log"
done
log "Proxy up."

# ── 5. Start SearXNG ─────────────────────────────────────────────────────────
if [ -d "$VENV_SEARXNG" ] && [ -d "$SEARXNG_SRC" ]; then
    log "Starting SearXNG..."
    fuser -k 8888/tcp 2>/dev/null || true
    cd "$SEARXNG_SRC"
    SEARXNG_SETTINGS_PATH="$DIR/searxng-settings.yml" \
    "$VENV_SEARXNG/bin/python" searx/webapp.py \
        > /tmp/loca-searxng.log 2>&1 &
    echo $! > /tmp/loca-searxng.pid
    cd "$DIR"
fi

log "Loca is ready. Open http://localhost:8000 in your browser."

# Keep alive
trap 'kill $(cat /tmp/loca-proxy.pid 2>/dev/null) $(cat /tmp/loca-searxng.pid 2>/dev/null) 2>/dev/null; exit' INT TERM
wait $PROXY_PID
