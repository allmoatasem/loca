"""
ModelManager — local model inventory and lifecycle.

Works with InferenceBackend to provide:
  - Listing locally downloaded models (GGUF + MLX)
  - Loading/switching the active model
  - Downloading models from Hugging Face
  - Deleting models

The routing system still uses Model enum values (general/code/reason) to
select system prompts and routing logic. The actual LLM is always whatever is
loaded in InferenceBackend — one model at a time.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from .inference_backend import InferenceBackend, InferenceBackendError
from .router import Model

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    name: str          # display name / filename without extension
    path: str          # absolute path to file or directory
    format: str        # "gguf" or "mlx"
    size_gb: float
    is_loaded: bool = False
    context_length: int | None = None  # max tokens from config
    param_label: str | None = None     # e.g. "7B", "70B", "3.8B"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "format": self.format,
            "size_gb": round(self.size_gb, 2),
            "is_loaded": self.is_loaded,
            "context_length": self.context_length,
            "param_label": self.param_label,
        }


@dataclass
class DownloadProgress:
    percent: float
    speed_mbps: float = 0.0
    eta_s: float = 0.0
    done: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 1),
            "speed_mbps": round(self.speed_mbps, 2),
            "eta_s": round(self.eta_s),
            "done": self.done,
            "error": self.error,
        }


class ModelManager:
    def __init__(self, config: dict, backend: InferenceBackend) -> None:
        self.backend = backend
        self.config = config
        inf = config.get("inference", {})
        self.models_dir = Path(inf.get("models_dir", "~/loca_models")).expanduser()
        self.gguf_dir = self.models_dir / "gguf"
        self.mlx_dir = self.models_dir / "mlx"
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # Local model inventory
    # ------------------------------------------------------------------

    def list_local(self) -> list[ModelInfo]:
        """Scan models_dir and return all discovered models."""
        models: list[ModelInfo] = []
        loaded_name = self.backend.current_model()

        # GGUF files
        if self.gguf_dir.exists():
            for p in sorted(self.gguf_dir.glob("*.gguf")):
                size_gb = p.stat().st_size / 1_073_741_824
                name = p.stem
                models.append(ModelInfo(
                    name=name,
                    path=str(p),
                    format="gguf",
                    size_gb=size_gb,
                    is_loaded=(loaded_name == p.name),
                    param_label=_extract_param_label(name),
                ))

        # MLX model directories (contain config.json)
        if self.mlx_dir.exists():
            for p in sorted(self.mlx_dir.iterdir()):
                if p.is_dir() and (p / "config.json").exists():
                    size_gb = _dir_size_gb(p)
                    name = p.name
                    ctx = _read_context_length(p / "config.json")
                    models.append(ModelInfo(
                        name=name,
                        path=str(p),
                        format="mlx",
                        size_gb=size_gb,
                        is_loaded=(loaded_name == p.name),
                        context_length=ctx,
                        param_label=_extract_param_label(name),
                    ))

        return models

    def get_model(self, name: str) -> ModelInfo | None:
        for m in self.list_local():
            if m.name == name:
                return m
        return None

    async def get_model_name(self, _model: Model) -> str:
        """Return the currently loaded model name (ignores routing — one model at a time)."""
        return self.backend.current_model() or ""

    async def get_model_api_base(self, _model: Model) -> str:
        """Return the inference backend API base URL."""
        return self.backend.api_base()

    # ------------------------------------------------------------------
    # Load / switch
    # ------------------------------------------------------------------

    async def load(self, model_name: str, ctx_size: int | None = None) -> tuple[str, str]:
        """Load a model by name into the inference backend."""
        model = self.get_model(model_name)
        if not model:
            raise InferenceBackendError(f"Model '{model_name}' not found in {self.models_dir}")
        await self.backend.restart(model.path, ctx_size)
        # Persist active model in config (relative path from models_dir)
        rel = Path(model.path).relative_to(self.models_dir)
        self.config.setdefault("inference", {})["active_model"] = str(rel)
        return model.name, self.backend.api_base()

    async def ensure_loaded(
        self,
        model: Model,
        model_name_override: str | None = None,
    ) -> tuple[str, str]:
        """
        Returns (model_path, api_base) for use in API calls.
        model_path is the full filesystem path — required by mlx_lm as the 'model' field.
        """
        # If override specified and different from current, switch
        if model_name_override and model_name_override != self.backend.current_model():
            local = self.get_model(model_name_override)
            if local:
                await self.backend.restart(local.path)
                return local.path, self.backend.api_base()
            # Override doesn't match a local model — log and continue with current

        if self.backend.is_running():
            return self.backend.current_model_path() or "local", self.backend.api_base()

        # Not running — try active_model from config
        active_rel = self.config.get("inference", {}).get("active_model")
        if active_rel:
            active_path = self.models_dir / active_rel
            if active_path.exists():
                logger.info(f"Auto-starting backend with: {active_path}")
                await self.backend.start(str(active_path))
                return self.backend.current_model_path() or "local", self.backend.api_base()

        # Fall back to first available local model
        local_models = self.list_local()
        if local_models:
            logger.info(f"Auto-starting backend with first available model: {local_models[0].name}")
            await self.backend.start(local_models[0].path)
            return self.backend.current_model_path() or "local", self.backend.api_base()

        raise InferenceBackendError(
            "No model is loaded and no models found in models_dir. "
            "Download a model first via the settings panel."
        )

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(
        self,
        repo_id: str,
        filename: str | None,
        target_format: str,
    ) -> AsyncGenerator[DownloadProgress, None]:
        """
        Download a model from Hugging Face, yielding progress updates.

        Args:
            repo_id: HF repo, e.g. "bartowski/Qwen2.5-7B-Instruct-GGUF"
            filename: specific file for GGUF, e.g. "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
                      None for MLX (downloads whole directory via snapshot_download)
            target_format: "gguf" or "mlx"
        """
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            yield DownloadProgress(0, error="huggingface_hub is not installed. Run: pip install huggingface-hub")
            return

        target_dir = self.gguf_dir if target_format == "gguf" else self.mlx_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            if target_format == "gguf" and filename:
                dest = target_dir / filename
                if dest.exists():
                    yield DownloadProgress(100, done=True)
                    return

                import httpx
                hf_url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
                async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                    async with client.stream("GET", hf_url) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length", 0))

                        # Pre-flight disk check
                        if total > 0:
                            free = shutil.disk_usage(target_dir).free
                            if total > free:
                                yield DownloadProgress(0, error=(
                                    f"Not enough disk space: model is {total/1e9:.1f} GB "
                                    f"but only {free/1e9:.1f} GB free"
                                ))
                                return

                        downloaded = 0
                        t0 = time.monotonic()
                        try:
                            with open(dest, "wb") as f:
                                async for chunk in resp.aiter_bytes(1024 * 1024):
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    elapsed = time.monotonic() - t0 or 0.001
                                    speed = downloaded / elapsed  # bytes/s
                                    speed_mb = speed / 1e6
                                    pct = (downloaded / total * 100) if total else -1
                                    eta = ((total - downloaded) / speed) if (total and speed > 0) else 0
                                    yield DownloadProgress(
                                        percent=pct,
                                        speed_mbps=round(speed_mb, 2),
                                        eta_s=round(eta),
                                    )
                        except Exception:
                            dest.unlink(missing_ok=True)
                            raise
                yield DownloadProgress(100, done=True)

            elif target_format == "mlx":
                model_dir_name = repo_id.split("/")[-1]
                dest = target_dir / model_dir_name
                if dest.exists() and (dest / "config.json").exists():
                    yield DownloadProgress(100, done=True)
                    return

                # Pre-flight disk check via HF API (best-effort)
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10) as client:
                        r = await client.get(f"https://huggingface.co/api/models/{repo_id}")
                        if r.status_code == 200:
                            siblings = r.json().get("siblings", [])
                            total_size = sum(s.get("size", 0) for s in siblings if s.get("size"))
                            if total_size > 0:
                                free = shutil.disk_usage(target_dir).free
                                if total_size > free:
                                    yield DownloadProgress(0, error=(
                                        f"Not enough disk space: model is {total_size/1e9:.1f} GB "
                                        f"but only {free/1e9:.1f} GB free"
                                    ))
                                    return
                except Exception:
                    pass  # best-effort — proceed if API call fails

                loop = asyncio.get_event_loop()

                def _snapshot() -> str:
                    return snapshot_download(
                        repo_id=repo_id,
                        local_dir=str(dest),
                    )

                task = loop.run_in_executor(None, _snapshot)
                while not task.done():
                    yield DownloadProgress(percent=-1)
                    await asyncio.sleep(1.0)
                await task
                yield DownloadProgress(100, done=True)

            else:
                yield DownloadProgress(0, error=f"Unknown format: {target_format}")

        except Exception as exc:
            logger.error(f"Download failed: {exc}")
            yield DownloadProgress(0, error=str(exc))

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, model_name: str) -> None:
        """Delete a local model by name. Raises if model is currently loaded."""
        model = self.get_model(model_name)
        if not model:
            raise FileNotFoundError(f"Model '{model_name}' not found")
        if model.is_loaded:
            raise InferenceBackendError(
                f"Cannot delete '{model_name}' while it is loaded. Load a different model first."
            )
        p = Path(model.path)
        if p.is_dir():
            shutil.rmtree(p)
            logger.info(f"Deleted MLX model directory: {p}")
        else:
            p.unlink()
            logger.info(f"Deleted GGUF file: {p}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        self.gguf_dir.mkdir(parents=True, exist_ok=True)
        self.mlx_dir.mkdir(parents=True, exist_ok=True)


def _dir_size_gb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / 1_073_741_824


def _extract_param_label(name: str) -> str | None:
    """Extract parameter count from model name, e.g. '7B', '70B', '3.8B'."""
    import re
    m = re.search(r'(\d+(?:\.\d+)?B(?:-A\d+(?:\.\d+)?B)?)', name, re.IGNORECASE)
    return m.group(0).upper() if m else None


def _read_context_length(config_path: Path) -> int | None:
    """Read max context length from an MLX model's config.json."""
    try:
        import json
        cfg = json.loads(config_path.read_text())
        return cfg.get("max_position_embeddings") or cfg.get("max_seq_len") or cfg.get("seq_length")
    except Exception:
        return None
