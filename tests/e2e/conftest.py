"""
Playwright end-to-end test fixtures.

Starts the real FastAPI app on a background thread with all external
side-effects mocked (no inference backend, no disk I/O, no network).
Provides a Playwright `page` fixture pointed at the running server.

Supports pytest-xdist: each worker gets its own port via worker_id.
"""

import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_BASE_PORT = 18923

_MINIMAL_CONFIG = {
    "inference": {"models_dir": "/tmp/loca-e2e-models", "active_model": None},
    "routing": {"max_tool_calls_per_turn": 5},
    "search": {"searxng_url": ""},
    "tools": {},
}


def _make_mocks():
    mock_backend = MagicMock()
    mock_backend.is_running.return_value = False
    mock_backend.current_model.return_value = None
    mock_backend.current_backend.return_value = None
    mock_backend.api_base.return_value = "http://localhost:11434"
    mock_backend.stop = AsyncMock()
    mock_backend.start = AsyncMock()
    mock_backend.models_dir = MagicMock()
    mock_backend.models_dir.__truediv__ = MagicMock(
        return_value=MagicMock(exists=MagicMock(return_value=False))
    )

    mock_mm = MagicMock()
    mock_mm.list_local.return_value = []
    mock_mm.load = AsyncMock(return_value=("test-model", "http://localhost:11434"))
    mock_mm.delete = MagicMock()
    mock_mm.download = AsyncMock()

    mock_orch = MagicMock()
    mock_orch.handle = AsyncMock()
    mock_orch.extract_and_save_memories = AsyncMock(return_value=[])

    return mock_backend, mock_mm, mock_orch


def _worker_port(worker_id):
    """Derive a unique port per xdist worker (or use base port if not parallel)."""
    if worker_id == "master" or not worker_id:
        return _BASE_PORT
    return _BASE_PORT + int(worker_id.replace("gw", "")) + 1


@pytest.fixture(scope="session")
def _server(worker_id):
    """Start FastAPI on a background thread; tear down after all tests."""
    port = _worker_port(worker_id)
    base = f"http://localhost:{port}"

    mock_backend, mock_mm, mock_orch = _make_mocks()

    with patch("src.proxy._load_config", return_value=_MINIMAL_CONFIG), \
         patch("src.proxy.InferenceBackend", return_value=mock_backend), \
         patch("src.proxy.ModelManager", return_value=mock_mm), \
         patch("src.proxy.Orchestrator", return_value=mock_orch), \
         patch("src.proxy._build_recs_cache", new_callable=AsyncMock):

        from src.proxy import app

        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning"
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        import httpx
        for _ in range(50):
            try:
                r = httpx.get(f"{base}/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError(f"E2E server did not start on port {port}")

        yield {"base_url": base}

        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def _browser():
    """One Chromium instance for the whole session."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def base_url(_server):
    return _server["base_url"]


def _setup_default_routes(pg, base):
    """
    Intercept API calls that the JS fires on page load.

    Uses route.fallback() so test-specific routes layered on top
    via page.route() take priority without needing page.unroute().
    """
    def _handle_models(route):
        try:
            route.fulfill(status=200, content_type="application/json", body='{"data": []}')
        except Exception:
            pass

    def _handle_conversations(route):
        try:
            if route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body='{"conversations": []}')
            else:
                route.fallback()
        except Exception:
            pass

    def _handle_memories(route):
        try:
            if route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body='{"memories": []}')
            else:
                route.fallback()
        except Exception:
            pass

    def _handle_stats(route):
        try:
            route.fulfill(status=200, content_type="application/json",
                          body='{"ram_used_gb": 8.2, "ram_total_gb": 32.0}')
        except Exception:
            pass

    def _handle_extract(route):
        try:
            route.fulfill(status=200, content_type="application/json",
                          body='{"memories": []}')
        except Exception:
            pass

    pg.route(f"{base}/v1/models", _handle_models)
    pg.route(f"{base}/api/conversations", _handle_conversations)
    pg.route(f"{base}/api/memories", _handle_memories)
    pg.route(f"{base}/system-stats", _handle_stats)
    pg.route(f"{base}/api/extract-memories", _handle_extract)


@pytest.fixture()
def page(_server, _browser):
    """Fresh browser context + page for each test."""
    context = _browser.new_context()
    pg = context.new_page()
    base = _server["base_url"]
    _setup_default_routes(pg, base)
    # Existing e2e tests target the pre-Svelte HTML (selectors like
    # #model-desc, #prefs-panel, .asst-content). After the Phase 5 cutover
    # `/` serves the Svelte bundle, so the legacy tests point at `/legacy`
    # where the old HTML still lives. Svelte e2e tests target `/ui` or `/`
    # directly and don't use this fixture.
    pg.goto(f"{base}/legacy")
    # Wait for JS to execute — the inline script sets model-desc text
    pg.wait_for_function("document.getElementById('model-desc')?.textContent?.length > 0")
    yield pg
    context.close()


@pytest.fixture()
def server_url(_server):
    return _server["base_url"]
