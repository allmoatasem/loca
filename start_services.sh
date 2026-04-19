#!/bin/bash

# Loca — macOS startup script
# Works both from the .app bundle (Resources/) and directly from the repo.
#
# When running from the bundle, all Python source is in Resources/src/.
# User data (DB, venv, searxng) lives in ~/Library/Application Support/Loca/.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Determine project root:
# - Bundle mode: src/ lives next to this script (in Resources/)
# - Dev mode: src/ is one level up from scripts/ or right here
if [ -d "$SCRIPT_DIR/src" ]; then
    DIR="$SCRIPT_DIR"           # bundled: Resources/ contains src/
else
    DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # dev: script is in repo root or subdir
fi

# User data lives in Application Support (writable, survives app updates)
LOCA_SUPPORT="$HOME/Library/Application Support/Loca"
VENV="$LOCA_SUPPORT/venv"
VENV_SEARXNG="$LOCA_SUPPORT/venv-searxng"
SEARXNG_SRC="$LOCA_SUPPORT/searxng"
export LOCA_DATA_DIR="$LOCA_SUPPORT/data"

mkdir -p "$LOCA_SUPPORT"

# Runtime files in user-owned directory (avoids world-readable /tmp symlink attacks)
LOCA_RUN="$LOCA_SUPPORT/run"
mkdir -p "$LOCA_RUN"
chmod 700 "$LOCA_RUN"

STATUS_FILE="$LOCA_RUN/startup-status.json"
PROXY_PID="$LOCA_RUN/proxy.pid"
PROXY_LOG="$LOCA_RUN/proxy.log"
SEARXNG_PID="$LOCA_RUN/searxng.pid"
SEARXNG_LOG="$LOCA_RUN/searxng.log"

# Symlink for backward compat with the Swift UI that reads /tmp/loca-startup-status.json
ln -sf "$STATUS_FILE" /tmp/loca-startup-status.json

# Reset status file so the app never reads a stale "Ready" from a previous run
printf '{"stage":"Initialising\u2026","progress":0}\n' > "$STATUS_FILE"

notify() {
    osascript -e "display notification \"$1\" with title \"Loca\" sound name \"Funk\""
}

status() {
    local msg="$1"
    local progress="${2:-0}"
    printf '{"stage":"%s","progress":%d}\n' "$msg" "$progress" > "$STATUS_FILE"
    notify "$msg"
}

shutdown() {
    notify "Shutting down Loca..."
    [ -f "$SEARXNG_PID" ] && kill "$(cat "$SEARXNG_PID")" 2>/dev/null || true
    [ -f "$PROXY_PID" ]   && kill "$(cat "$PROXY_PID")"   2>/dev/null || true
    rm -f "$PROXY_PID" "$SEARXNG_PID" /tmp/loca-startup-status.json
    exit 0
}

bail() {
    osascript -e "display alert \"Loca failed to start\" message \"$1\" as critical"
    shutdown
}

# ── 1. Check inference backend binaries ──────────────────────────────────────
BREW=""
for _b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$_b" ] && BREW="$_b" && break
done

if ! command -v llama-server > /dev/null 2>&1; then
    if [ -n "$BREW" ]; then
        status "Installing llama.cpp…" 5
        "$BREW" install llama.cpp || bail "Failed to install llama.cpp via Homebrew."
    else
        bail "llama-server not found and Homebrew is not installed. Install Homebrew first: https://brew.sh"
    fi
fi

# espeak-ng — G2P fallback for Kokoro TTS via misaki. Without it, misaki
# logs "OOD words will be skipped" and silently returns None for out-of-
# dictionary tokens, which then crashes Kokoro's pipeline with
# `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`
# on the second synthesis call. Soft-fail: if Homebrew isn't available,
# voice mode still works for basic dictionary text — it just breaks on
# uncommon words until the user installs espeak-ng themselves.
if ! command -v espeak-ng > /dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/espeak-ng ] \
   && [ ! -x /usr/local/bin/espeak-ng ]; then
    if [ -n "$BREW" ]; then
        status "Installing espeak-ng (voice fallback)…" 7
        "$BREW" install espeak-ng || true
    fi
fi

# ── 2. Python venv ────────────────────────────────────────────────────────────
# Prefer Python 3.10+ (required for X | Y union syntax); fall back to python3.
PYTHON3=""
for _p in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$_p" > /dev/null 2>&1; then
        _ver=$("$_p" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null)
        if [ "$_ver" = "True" ]; then
            PYTHON3="$_p"
            break
        fi
    fi
done
[ -z "$PYTHON3" ] && bail "Python 3.10 or later is required. Install via Homebrew: brew install python3"

_venv_ok=0
if [ -d "$VENV" ] \
   && "$VENV/bin/python" -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null \
   && "$VENV/bin/pip" --version > /dev/null 2>&1; then
    _venv_ok=1
fi

if [ "$_venv_ok" -eq 0 ]; then
    status "Setting up Python environment…" 10
    rm -rf "$VENV"
    "$PYTHON3" -m venv "$VENV" || bail "Failed to create Python venv."
    status "Installing dependencies…" 20
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install dependencies."
elif [ "$DIR/requirements.txt" -nt "$VENV/bin/pip" ]; then
    status "Installing new dependencies…" 20
    "$VENV/bin/pip" install -r "$DIR/requirements.txt" -q || bail "Failed to install dependencies."
fi

# spaCy English model — required by `misaki` (Kokoro's G2P frontend).
# Without this, the first TTS synthesis in the app falls into misaki's
# runtime auto-download path which invokes `spacy cli.download`, which
# invokes pip — and pip can fail in the bundled venv, crashing the TTS
# request with a 500. Pre-installing closes that failure mode for good.
if ! "$VENV/bin/python" -c "import en_core_web_sm" 2>/dev/null; then
    status "Installing speech model…" 25
    "$VENV/bin/python" -m spacy download en_core_web_sm -q \
        || bail "Failed to install spaCy English model (needed for voice TTS)."
fi

# ── 3. SearXNG setup (first-run) ─────────────────────────────────────────────
PYTHON312=$(command -v python3.12 2>/dev/null || true)
if [ -z "$PYTHON312" ]; then
    for p in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        [ -f "$p" ] && PYTHON312="$p" && break
    done
fi

if [ -n "$PYTHON312" ]; then
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
fi

# ── 4. Start orchestrator proxy ───────────────────────────────────────────────
status "Starting Loca…" 60
lsof -ti tcp:8000 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
lsof -ti tcp:8080 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
cd "$DIR"
LOCA_DATA_DIR="$LOCA_DATA_DIR" \
"$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
    > "$PROXY_LOG" 2>&1 &
echo $! > "$PROXY_PID"

for i in $(seq 1 30); do
    sleep 1
    curl -s http://localhost:8000/health > /dev/null 2>&1 && break
    [ "$i" -eq 30 ] && bail "Proxy didn't start. Check $PROXY_LOG"
done

# ── 5. Start SearXNG ─────────────────────────────────────────────────────────
if [ -n "$PYTHON312" ] && [ -d "$VENV_SEARXNG" ] && [ -d "$SEARXNG_SRC" ]; then
    status "Starting search…" 80
    lsof -ti tcp:8888 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
    cd "$SEARXNG_SRC"
    SEARXNG_SETTINGS_PATH="$DIR/searxng-settings.yml" \
    "$VENV_SEARXNG/bin/python" searx/webapp.py \
        > "$SEARXNG_LOG" 2>&1 &
    echo $! > "$SEARXNG_PID"
    cd "$DIR"

    for i in $(seq 1 30); do
        sleep 2
        curl -s http://127.0.0.1:8888/ > /dev/null 2>&1 && break
        [ "$i" -eq 30 ] && bail "SearXNG didn't start. Check $SEARXNG_LOG"
    done
fi

# ── 6. Signal ready ──────────────────────────────────────────────────────────
status "Ready" 100

# ── Keep alive + watchdog ─────────────────────────────────────────────────────
trap shutdown INT TERM EXIT

while true; do
    sleep 15

    if ! kill -0 "$(cat "$PROXY_PID" 2>/dev/null)" 2>/dev/null; then
        cd "$DIR"
        LOCA_DATA_DIR="$LOCA_DATA_DIR" \
        "$VENV/bin/python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 \
            >> "$PROXY_LOG" 2>&1 &
        echo $! > "$PROXY_PID"
    fi

    if [ -n "$PYTHON312" ] && [ -d "$VENV_SEARXNG" ] && [ -d "$SEARXNG_SRC" ]; then
        if ! kill -0 "$(cat "$SEARXNG_PID" 2>/dev/null)" 2>/dev/null; then
            cd "$SEARXNG_SRC"
            SEARXNG_SETTINGS_PATH="$DIR/searxng-settings.yml" \
            "$VENV_SEARXNG/bin/python" searx/webapp.py \
                >> "$SEARXNG_LOG" 2>&1 &
            echo $! > "$SEARXNG_PID"
            cd "$DIR"
        fi
    fi
done
