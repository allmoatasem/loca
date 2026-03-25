#!/bin/bash

# Resolve DIR relative to this script — works wherever the repo is cloned
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
LMS="$HOME/.lmstudio/bin/lms"
VENV="$DIR/.venv"
VENV_SEARXNG="$DIR/.venv-searxng"
SEARXNG_SRC="$DIR/searxng"

# Find Python 3.12 — check PATH first, then common Homebrew locations
# (Apple Silicon: /opt/homebrew, Intel: /usr/local)
PYTHON312=$(command -v python3.12 2>/dev/null || true)
if [ -z "$PYTHON312" ]; then
    for p in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        [ -f "$p" ] && PYTHON312="$p" && break
    done
fi

notify() {
    osascript -e "display notification \"$1\" with title \"Loca\" sound name \"Funk\""
}

shutdown() {
    notify "Shutting down Loca..."
    [ -f /tmp/loca-searxng.pid ] && kill "$(cat /tmp/loca-searxng.pid)" 2>/dev/null || true
    [ -f /tmp/loca-proxy.pid ]   && kill "$(cat /tmp/loca-proxy.pid)"   2>/dev/null || true
    [ -f "$LMS" ] && "$LMS" server stop 2>/dev/null || true
    exit 0
}

bail() {
    osascript -e "display alert \"Loca failed to start\" message \"$1\" as critical"
    shutdown
}

# ── 1. LM Studio server ──────────────────────────────────────────────────────
notify "Starting LM Studio..."
if [ ! -f "$LMS" ]; then
    bail "LM Studio CLI not found. Install LM Studio from lmstudio.ai."
fi

if ! pgrep -x "LM Studio" > /dev/null 2>&1; then
    open -a "LM Studio"
    sleep 10
fi

for attempt in 1 2 3; do
    "$LMS" server start 2>/dev/null || true
    sleep 4
    "$LMS" server status 2>&1 | grep -q "running" && break
    [ "$attempt" -eq 3 ] || sleep 6
done

for i in $(seq 1 90); do
    sleep 2
    curl -s http://localhost:1234/v1/models > /dev/null 2>&1 && break
    [ $((i % 15)) -eq 0 ] && "$LMS" server start 2>/dev/null || true
    if [ "$i" -eq 90 ]; then
        bail "LM Studio server isn't responding on port 1234 after 3 min. Open LM Studio manually and check the server tab."
    fi
done

# ── 2. Orchestrator venv (first-run setup) ───────────────────────────────────
if [ ! -d "$VENV" ]; then
    notify "First run: setting up orchestrator (30s)..."
    python3 -m venv "$VENV" || bail "Failed to create Python venv."
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install orchestrator dependencies."
fi

# ── 3. SearXNG setup (first-run) ─────────────────────────────────────────────
if [ -z "$PYTHON312" ] || [ ! -f "$PYTHON312" ]; then
    bail "Python 3.12 not found. Run: brew install python@3.12"
fi

if [ ! -d "$SEARXNG_SRC" ]; then
    notify "First run: cloning SearXNG..."
    git clone --depth=1 https://github.com/searxng/searxng "$SEARXNG_SRC" 2>/dev/null \
        || bail "Failed to clone SearXNG. Check internet connection."
fi

if [ ! -d "$VENV_SEARXNG" ]; then
    notify "First run: installing SearXNG (2-3 min)..."
    "$PYTHON312" -m venv "$VENV_SEARXNG" || bail "Failed to create SearXNG venv."
    "$VENV_SEARXNG/bin/pip" install -U pip setuptools wheel pyyaml -q \
        || bail "Failed to upgrade pip for SearXNG."
    "$VENV_SEARXNG/bin/pip" install -r "$SEARXNG_SRC/requirements.txt" -q \
        || bail "Failed to install SearXNG requirements."
    "$VENV_SEARXNG/bin/pip" install --use-pep517 --no-build-isolation -e "$SEARXNG_SRC" -q \
        || bail "Failed to install SearXNG."
fi

# ── 4. Sync model list from LM Studio → config.yaml ──────────────────────────
notify "Syncing models..."
cd "$DIR"
"$VENV/bin/python" src/model_sync.py >> /tmp/loca-proxy.log 2>&1 || true

# ── 5. Start orchestrator proxy ───────────────────────────────────────────────
notify "Starting orchestrator proxy..."
lsof -ti tcp:8000 | xargs kill -9 2>/dev/null || true
cd "$DIR"
"$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
    > /tmp/loca-proxy.log 2>&1 &
echo $! > /tmp/loca-proxy.pid

for i in $(seq 1 20); do
    sleep 1
    curl -s http://localhost:8000/health > /dev/null 2>&1 && break
    [ "$i" -eq 20 ] && bail "Orchestrator proxy didn't start. Check /tmp/loca-proxy.log"
done

# ── 6. Start SearXNG ─────────────────────────────────────────────────────────
notify "Starting SearXNG..."
lsof -ti tcp:8888 | xargs kill -9 2>/dev/null || true
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

# ── 7. Open the UI ────────────────────────────────────────────────────────────
open http://localhost:8000
notify "Loca is ready."

# ── Keep alive + watchdog ─────────────────────────────────────────────────────
trap shutdown INT TERM EXIT

while true; do
    sleep 15

    # LM Studio server watchdog
    if [ -f "$LMS" ] && ! "$LMS" server status 2>&1 | grep -q "running"; then
        notify "LM Studio server stopped — restarting..."
        if ! pgrep -x "LM Studio" > /dev/null 2>&1; then
            open -a "LM Studio"
            sleep 10
        fi
        for attempt in 1 2 3; do
            "$LMS" server start 2>/dev/null || true
            sleep 4
            "$LMS" server status 2>&1 | grep -q "running" && break
        done
        for j in $(seq 1 30); do
            sleep 2
            curl -s http://localhost:1234/v1/models > /dev/null 2>&1 && break
        done
    fi

    # Orchestrator proxy watchdog
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
