"""
Playwright end-to-end test fixtures.

Starts the real FastAPI app on a background thread with all external
side-effects mocked (no inference backend, no disk I/O, no network).
Provides a Playwright `page` fixture pointed at the running server.
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

E2E_PORT = 18923  # unlikely to collide
E2E_BASE = f"http://localhost:{E2E_PORT}"

# ── Minimal config identical to test_proxy.py ────────────────────────────────

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


# ── Session-scoped: one server for all e2e tests ────────────────────────────

@pytest.fixture(scope="session")
def _server():
    """Start FastAPI on a background thread; tear down after all tests."""
    mock_backend, mock_mm, mock_orch = _make_mocks()

    with patch("src.proxy._load_config", return_value=_MINIMAL_CONFIG), \
         patch("src.proxy.InferenceBackend", return_value=mock_backend), \
         patch("src.proxy.ModelManager", return_value=mock_mm), \
         patch("src.proxy.Orchestrator", return_value=mock_orch), \
         patch("src.proxy._build_recs_cache", new_callable=AsyncMock), \
         patch("asyncio.create_task"):

        from src.proxy import app

        config = uvicorn.Config(
            app, host="127.0.0.1", port=E2E_PORT, log_level="warning"
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait until the server is accepting connections
        import httpx
        for _ in range(50):
            try:
                r = httpx.get(f"{E2E_BASE}/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("E2E server did not start in time")

        yield {
            "base_url": E2E_BASE,
            "mock_backend": mock_backend,
            "mock_mm": mock_mm,
            "mock_orch": mock_orch,
        }

        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def _browser():
    """One Chromium instance for the whole session."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


# Override pytest-playwright's base_url at session scope to avoid ScopeMismatch
@pytest.fixture(scope="session")
def base_url(_server):
    return _server["base_url"]


@pytest.fixture()
def page(_server, _browser):
    """
    Fresh browser context + page for each test.
    Intercepts API calls that the UI fires on load so the page renders cleanly.
    """
    context = _browser.new_context()
    pg = context.new_page()

    base = _server["base_url"]

    # Intercept endpoints that the JS calls on page load so the UI
    # doesn't stall waiting for backends that aren't running.

    # /v1/models → empty model list
    pg.route(f"{base}/v1/models", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body='{"data": []}',
    ))

    # /api/conversations → empty list
    pg.route(f"{base}/api/conversations", lambda route: (
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"conversations": []}',
        ) if route.request.method == "GET" else route.continue_()
    ))

    # /api/memories → empty list
    pg.route(f"{base}/api/memories", lambda route: (
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"memories": []}',
        ) if route.request.method == "GET" else route.continue_()
    ))

    # /system-stats → fake RAM stats
    pg.route(f"{base}/system-stats", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body='{"ram_used_gb": 8.2, "ram_total_gb": 32.0}',
    ))

    pg.goto(base)
    pg.wait_for_load_state("networkidle")

    yield pg

    context.close()


@pytest.fixture()
def server_url(_server):
    """The server base URL for use in test route interceptions."""
    return _server["base_url"]
