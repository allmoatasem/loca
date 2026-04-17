"""Take a screenshot of a Loca UI route and save it under docs/screenshots/ui/.

Used to attach visuals to PRs during the Svelte migration. Spins up the real
FastAPI app on a background thread with external side-effects mocked — same
pattern tests/e2e/conftest.py uses. Requires the UI to have been built
already (`npm run build --prefix ui`); otherwise the /ui route 404s.

Usage:
    .venv/bin/python scripts/screenshot_ui.py <route> <filename> [--dark]

Example:
    .venv/bin/python scripts/screenshot_ui.py /ui/glossary glossary

Writes:
    docs/screenshots/ui/<filename>.png          (light)
    docs/screenshots/ui/<filename>-dark.png     (when --dark)
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import uvicorn
from playwright.sync_api import sync_playwright

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_OUT_DIR = _ROOT / "docs" / "screenshots" / "ui"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> threading.Thread:
    """Mimic the e2e conftest: minimal mocked backend, real FastAPI app."""
    def _mocks():
        backend = MagicMock()
        backend.is_running.return_value = False
        backend.current_model.return_value = None
        backend.current_backend.return_value = None
        backend.api_base.return_value = f"http://localhost:{port}"
        backend.stop = AsyncMock()
        backend.start = AsyncMock()
        backend.models_dir = MagicMock()

        mm = MagicMock()
        mm.list_local.return_value = []
        mm.load = AsyncMock(return_value=("test-model", f"http://localhost:{port}"))

        orch = MagicMock()
        orch.handle = AsyncMock()
        orch.extract_and_save_memories = AsyncMock(return_value=[])

        voice = MagicMock()
        voice.transcribe = AsyncMock(return_value={"text": ""})
        voice.synthesize = AsyncMock(return_value=b"")
        voice.get_voice_config.return_value = {
            "stt_model": "", "tts_model": "", "tts_voice": "",
            "tts_speed": 1.0, "auto_tts": False, "models": [],
        }
        voice.list_voice_models.return_value = []

        memory = MagicMock()
        memory.recall = AsyncMock(return_value=[])
        plugin_mgr = MagicMock()
        plugin_mgr.memory_plugin = memory
        plugin_mgr.start = AsyncMock()
        plugin_mgr.stop = AsyncMock()
        plugin_mgr.status.return_value = {"memory": {"type": "builtin", "status": "running"}}

        return backend, mm, orch, voice, plugin_mgr

    backend, mm, orch, voice, plugin_mgr = _mocks()

    config = {
        "inference": {"models_dir": "/tmp/loca-ui-shot", "active_model": None},
        "routing": {"max_tool_calls_per_turn": 5},
        "search": {"searxng_url": ""},
        "tools": {},
    }

    os.environ["ORCHESTRATOR_CONFIG"] = "/tmp/loca-ui-shot-config.yaml"

    patchers = [
        patch("src.proxy._load_config", return_value=config),
        patch("src.proxy.InferenceBackend", return_value=backend),
        patch("src.proxy.ModelManager", return_value=mm),
        patch("src.proxy.Orchestrator", return_value=orch),
        patch("src.proxy.VoiceBackend", return_value=voice),
        patch("src.proxy.PluginManager", return_value=plugin_mgr),
        patch("src.proxy._build_recs_cache", new_callable=AsyncMock),
    ]
    for p in patchers:
        p.start()

    from src.proxy import app

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)

    def _run() -> None:
        server.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Wait until the server is accepting connections
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return t
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def _capture(route: str, out_path: Path, *, dark: bool) -> None:
    port = _free_port()
    _start_server(port)
    url = f"http://127.0.0.1:{port}{route}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": 900, "height": 640},
            device_scale_factor=2,
            color_scheme="dark" if dark else "light",
        )
        page = context.new_page()
        page.goto(url)
        # Wait for any data-loading to settle. On the scaffolding/Glossary
        # pages the backend /health ping is the only async work.
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(200)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_path), full_page=False)
        browser.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("route", help="Path to screenshot, e.g. /ui/glossary")
    p.add_argument("name", help="Output filename (without extension)")
    p.add_argument("--dark", action="store_true",
                   help="Also capture a dark-mode variant as <name>-dark.png")
    args = p.parse_args()

    light = _OUT_DIR / f"{args.name}.png"
    _capture(args.route, light, dark=False)
    print(f"wrote {light.relative_to(_ROOT)}")

    if args.dark:
        dark = _OUT_DIR / f"{args.name}-dark.png"
        _capture(args.route, dark, dark=True)
        print(f"wrote {dark.relative_to(_ROOT)}")

    os._exit(0)  # bypass uvicorn thread that won't exit cleanly


if __name__ == "__main__":
    raise SystemExit(main())
