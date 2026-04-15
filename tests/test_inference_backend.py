"""
Tests for InferenceBackend.

Run with: pytest tests/test_inference_backend.py -v
"""

import os
import platform
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference_backend import InferenceBackend, InferenceBackendError

BASE_CONFIG = {
    "inference": {
        "port": 18080,
        "models_dir": "/tmp/test_loca_models",
        "ctx_size": 4096,
        "backend": "auto",
        "llama_server": "llama-server",
    }
}


def make_backend(cfg=None):
    return InferenceBackend(cfg or BASE_CONFIG)


# ── Backend detection ─────────────────────────────────────────────────────────

def test_detect_gguf_file():
    b = make_backend()
    assert b._detect_backend("/models/llama-3b.gguf") == "llama.cpp"


def test_detect_gguf_case_insensitive():
    b = make_backend()
    assert b._detect_backend("/models/MODEL.GGUF") == "llama.cpp"


def test_detect_mlx_dir_on_arm64(tmp_path):
    model_dir = tmp_path / "mlx-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")

    b = make_backend()
    if platform.machine() == "arm64" and sys.platform == "darwin":
        assert b._detect_backend(str(model_dir)) == "mlx"
    else:
        with pytest.raises(InferenceBackendError, match="Apple Silicon"):
            b._detect_backend(str(model_dir))


def test_detect_dir_with_gguf_inside(tmp_path):
    model_dir = tmp_path / "gguf-dir"
    model_dir.mkdir()
    (model_dir / "model.gguf").write_text("")

    b = make_backend()
    assert b._detect_backend(str(model_dir)) == "llama.cpp"


def test_detect_unknown_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    b = make_backend()
    with pytest.raises(InferenceBackendError):
        b._detect_backend(str(empty_dir))


def test_preferred_backend_override_mlx():
    cfg = dict(BASE_CONFIG)
    cfg["inference"] = {**BASE_CONFIG["inference"], "backend": "mlx"}
    b = make_backend(cfg)
    # forced to mlx regardless of path
    assert b._detect_backend("/models/model.gguf") == "mlx"


def test_preferred_backend_override_llama():
    cfg = dict(BASE_CONFIG)
    cfg["inference"] = {**BASE_CONFIG["inference"], "backend": "llama.cpp"}
    b = make_backend(cfg)
    assert b._detect_backend("/models/anything") == "llama.cpp"


# ── Argument building ──────────────────────────────────────────────────────────

def test_llama_args_structure():
    b = make_backend()
    args = b._build_llama_args("/models/llama.gguf", 8192)
    assert "llama-server" in args
    assert "-m" in args
    assert "--ctx-size" in args
    assert "8192" in args
    assert "--port" in args
    assert str(18080) in args


def test_mlx_args_structure():
    b = make_backend()
    args = b._build_mlx_args("/models/mlx-model", 4096)
    # Either "mlx_lm server" script path or "python -m mlx_lm server"
    combined = " ".join(args)
    assert "mlx_lm" in combined
    assert "server" in combined
    assert "--port" in args
    assert str(18080) in args


# ── Health check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_returns_true_on_200():
    b = make_backend()
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await b.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_connection_error():
    b = make_backend()
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        result = await b.health_check()
    assert result is False


# ── is_running ────────────────────────────────────────────────────────────────

def test_is_running_false_when_no_proc():
    b = make_backend()
    assert b.is_running() is False


def test_is_running_false_when_proc_exited():
    b = make_backend()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    b._proc = mock_proc
    assert b.is_running() is False


def test_is_running_true_when_proc_alive():
    b = make_backend()
    mock_proc = MagicMock()
    mock_proc.returncode = None
    b._proc = mock_proc
    assert b.is_running() is True


# ── api_base ──────────────────────────────────────────────────────────────────

def test_api_base():
    b = make_backend()
    assert b.api_base() == "http://localhost:18080"


# ── stop ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_terminates_process():
    b = make_backend()
    mock_proc = MagicMock()  # MagicMock so terminate() is sync (matches real subprocess)
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=None)
    b._proc = mock_proc
    b._current_model = "test.gguf"

    await b.stop()

    mock_proc.terminate.assert_called_once()
    assert b._proc is None
    assert b._current_model is None


# ── LM Studio mode ────────────────────────────────────────────────────────────

LM_STUDIO_CONFIG = {
    "inference": {
        "port": 18080,
        "models_dir": "/tmp/test_loca_models",
        "ctx_size": 4096,
        "backend": "auto",
        "llama_server": "llama-server",
        "lm_studio": True,
        "lm_studio_url": "http://localhost:1234",
    }
}


def test_lm_studio_mode_api_base():
    b = InferenceBackend(LM_STUDIO_CONFIG)
    assert b.lm_studio_mode is True
    assert b.api_base() == "http://localhost:1234"


def test_lm_studio_mode_is_running_returns_true():
    b = InferenceBackend(LM_STUDIO_CONFIG)
    assert b.is_running() is True


@pytest.mark.asyncio
async def test_lm_studio_mode_start_is_noop():
    b = InferenceBackend(LM_STUDIO_CONFIG)
    # Should not raise; no subprocess is started
    await b.start("/some/model.gguf")
    assert b._proc is None


@pytest.mark.asyncio
async def test_lm_studio_mode_stop_is_noop():
    b = InferenceBackend(LM_STUDIO_CONFIG)
    # Should not raise even with no process
    await b.stop()
    assert b._proc is None


def test_native_mode_api_base():
    b = make_backend()
    assert b.api_base() == "http://localhost:18080"


def test_lm_studio_defaults_to_false():
    b = make_backend()
    assert b.lm_studio_mode is False
    assert b.lm_studio_url == "http://localhost:1234"
