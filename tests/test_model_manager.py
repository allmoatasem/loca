"""
Tests for ModelManager.

Run with: pytest tests/test_model_manager.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.inference_backend import InferenceBackend, InferenceBackendError
from src.model_manager import ModelManager


def make_config(models_dir: str) -> dict:
    return {
        "inference": {
            "port": 18080,
            "models_dir": models_dir,
            "ctx_size": 4096,
            "backend": "auto",
            "llama_server": "llama-server",
            "active_model": None,
        }
    }


def make_manager(tmp_path) -> tuple[ModelManager, MagicMock]:
    cfg = make_config(str(tmp_path))
    mock_backend = MagicMock(spec=InferenceBackend)
    mock_backend.current_model.return_value = None
    mock_backend.is_running.return_value = False
    mock_backend.api_base.return_value = "http://localhost:18080"
    mock_backend.restart = AsyncMock()
    mock_backend.start = AsyncMock()
    mock_backend.health_check = AsyncMock(return_value=False)
    mm = ModelManager(cfg, mock_backend)
    return mm, mock_backend


# ── list_local ────────────────────────────────────────────────────────────────

def test_list_local_gguf(tmp_path):
    mm, backend = make_manager(tmp_path)
    gguf_file = mm.gguf_dir / "llama-3b-q4.gguf"
    gguf_file.write_bytes(b"x" * (1024 * 1024 * 100))  # 100 MB fake model

    models = mm.list_local()
    assert len(models) == 1
    assert models[0].format == "gguf"
    assert models[0].name == "llama-3b-q4"
    assert models[0].size_gb > 0


def test_list_local_mlx(tmp_path):
    mm, backend = make_manager(tmp_path)
    mlx_dir = mm.mlx_dir / "mlx-community--Llama-3-8B-4bit"
    mlx_dir.mkdir()
    (mlx_dir / "config.json").write_text('{"model_type": "llama"}')
    (mlx_dir / "model.safetensors").write_bytes(b"x" * 1024)

    models = mm.list_local()
    assert len(models) == 1
    assert models[0].format == "mlx"
    assert models[0].name == "mlx-community--Llama-3-8B-4bit"


def test_list_local_empty(tmp_path):
    mm, _ = make_manager(tmp_path)
    assert mm.list_local() == []


def test_list_local_marks_loaded(tmp_path):
    mm, backend = make_manager(tmp_path)
    gguf_file = mm.gguf_dir / "active.gguf"
    gguf_file.write_bytes(b"x" * 1024)
    backend.current_model.return_value = "active.gguf"

    models = mm.list_local()
    assert models[0].is_loaded is True


def test_list_local_mlx_dir_without_config_skipped(tmp_path):
    mm, _ = make_manager(tmp_path)
    bad_dir = mm.mlx_dir / "not-a-model"
    bad_dir.mkdir()
    (bad_dir / "random.txt").write_text("nothing")

    models = mm.list_local()
    assert len(models) == 0


# ── get_model ─────────────────────────────────────────────────────────────────

def test_get_model_found(tmp_path):
    mm, _ = make_manager(tmp_path)
    (mm.gguf_dir / "test.gguf").write_bytes(b"x" * 1024)

    m = mm.get_model("test")
    assert m is not None
    assert m.name == "test"


def test_get_model_not_found(tmp_path):
    mm, _ = make_manager(tmp_path)
    assert mm.get_model("nonexistent") is None


# ── load ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_calls_backend_restart(tmp_path):
    mm, backend = make_manager(tmp_path)
    (mm.gguf_dir / "model.gguf").write_bytes(b"x" * 1024)
    backend.current_model.return_value = "model.gguf"
    backend.api_base.return_value = "http://localhost:18080"

    name, api_base = await mm.load("model")
    backend.restart.assert_called_once()
    assert "model" in name
    assert "18080" in api_base


@pytest.mark.asyncio
async def test_load_raises_if_model_not_found(tmp_path):
    mm, _ = make_manager(tmp_path)
    with pytest.raises(InferenceBackendError, match="not found"):
        await mm.load("ghost-model")


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_gguf(tmp_path):
    mm, backend = make_manager(tmp_path)
    gguf_file = mm.gguf_dir / "to-delete.gguf"
    gguf_file.write_bytes(b"x" * 1024)
    backend.current_model.return_value = None

    mm.delete("to-delete")
    assert not gguf_file.exists()


def test_delete_mlx_dir(tmp_path):
    mm, backend = make_manager(tmp_path)
    mlx_dir = mm.mlx_dir / "mlx-model"
    mlx_dir.mkdir()
    (mlx_dir / "config.json").write_text("{}")
    backend.current_model.return_value = None

    mm.delete("mlx-model")
    assert not mlx_dir.exists()


def test_delete_raises_if_model_loaded(tmp_path):
    mm, backend = make_manager(tmp_path)
    (mm.gguf_dir / "active.gguf").write_bytes(b"x" * 1024)
    backend.current_model.return_value = "active.gguf"

    with pytest.raises(InferenceBackendError, match="Cannot delete"):
        mm.delete("active")


def test_delete_raises_if_not_found(tmp_path):
    mm, _ = make_manager(tmp_path)
    with pytest.raises(FileNotFoundError):
        mm.delete("ghost")


# ── ensure_loaded ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_loaded_returns_running_backend(tmp_path):
    from src.router import Model
    mm, backend = make_manager(tmp_path)
    backend.is_running.return_value = True
    backend.current_model.return_value = "mymodel.gguf"
    backend.current_model_path.return_value = "/models/gguf/mymodel.gguf"

    name, api_base = await mm.ensure_loaded(Model.GENERAL)
    assert name == "/models/gguf/mymodel.gguf"
    backend.start.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_loaded_raises_when_nothing_loaded(tmp_path):
    """ensure_loaded no longer auto-starts — user must load explicitly."""
    from src.router import Model
    from src.inference_backend import InferenceBackendError
    cfg = make_config(str(tmp_path))
    cfg["inference"]["active_model"] = "gguf/starter.gguf"
    mock_backend = MagicMock(spec=InferenceBackend)
    mock_backend.is_running.return_value = False
    mock_backend.current_model.return_value = None
    mock_backend.api_base.return_value = "http://localhost:18080"
    mock_backend.start = AsyncMock()
    mock_backend.restart = AsyncMock()
    mm = ModelManager(cfg, mock_backend)

    gguf_file = mm.gguf_dir / "starter.gguf"
    gguf_file.write_bytes(b"x" * 1024)

    with pytest.raises(InferenceBackendError, match="No model is loaded"):
        await mm.ensure_loaded(Model.GENERAL)
    mock_backend.start.assert_not_called()
