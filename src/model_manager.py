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
    supports_vision: bool = False      # detected from config.json vision_config

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "format": self.format,
            "size_gb": round(self.size_gb, 2),
            "is_loaded": self.is_loaded,
            "context_length": self.context_length,
            "param_label": self.param_label,
            "supports_vision": self.supports_vision,
        }


@dataclass
class DownloadProgress:
    percent: float
    speed_mbps: float = 0.0
    eta_s: float = 0.0
    done: bool = False
    error: str | None = None
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 1),
            "speed_mbps": round(self.speed_mbps, 2),
            "eta_s": round(self.eta_s),
            "done": self.done,
            "error": self.error,
            "total_bytes": self.total_bytes,
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
                    cfg_path = p / "config.json"
                    ctx = _read_context_length(cfg_path)
                    vision = _has_vision_config(cfg_path)
                    models.append(ModelInfo(
                        name=name,
                        path=str(p),
                        format="mlx",
                        size_gb=size_gb,
                        is_loaded=(loaded_name == p.name),
                        context_length=ctx,
                        param_label=_extract_param_label(name),
                        supports_vision=vision,
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

    async def load(
        self,
        model_name: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> tuple[str, str]:
        """Load a model by name into the inference backend."""
        model = self.get_model(model_name)
        if not model:
            raise InferenceBackendError(f"Model '{model_name}' not found in {self.models_dir}")
        await self.backend.restart(model.path, ctx_size, n_gpu_layers, batch_size, num_threads)
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
            import httpx as _httpx_check  # noqa: F401 — verify httpx is available
        except ImportError:
            yield DownloadProgress(0, error="httpx is not installed. Run: pip install httpx")
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

                # Get file list + sizes from HF API (retry once on failure)
                import httpx
                siblings: list[dict] = []
                for attempt in range(2):
                    try:
                        async with httpx.AsyncClient(timeout=15) as client:
                            r = await client.get(f"https://huggingface.co/api/models/{repo_id}")
                            if r.status_code == 200:
                                siblings = [
                                    s for s in r.json().get("siblings", [])
                                    if not s["rfilename"].endswith(".gitattributes")
                                ]
                                break
                    except Exception as e:
                        logger.warning(f"HF API attempt {attempt + 1} failed: {e}")
                        if attempt == 0:
                            await asyncio.sleep(2)

                if not siblings:
                    yield DownloadProgress(0, error="Could not fetch file list from Hugging Face API")
                    return

                # Build size map from API (may be 0 for repos that omit sizes)
                actual_sizes: dict[str, int] = {s["rfilename"]: s.get("size", 0) for s in siblings}

                # HEAD any files with missing sizes in parallel before starting
                missing = [fn for fn, sz in actual_sizes.items() if sz == 0]
                if missing:
                    yield DownloadProgress(percent=-1)  # spinner while resolving
                    resolved = await _resolve_file_sizes(repo_id, missing)
                    actual_sizes.update(resolved)

                total_size = sum(actual_sizes.values())

                # Disk check
                if total_size > 0:
                    free = shutil.disk_usage(target_dir).free
                    if total_size > free:
                        yield DownloadProgress(0, error=(
                            f"Not enough disk space: model is {total_size/1e9:.1f} GB "
                            f"but only {free/1e9:.1f} GB free"
                        ))
                        return

                # Parallel download — up to 4 files at once (matches LM Studio behaviour)
                dest.mkdir(parents=True, exist_ok=True)
                downloaded_bytes: dict[str, int] = {}  # rfilename → bytes received
                t0 = time.monotonic()
                sem = asyncio.Semaphore(4)
                progress_queue: asyncio.Queue[DownloadProgress] = asyncio.Queue()

                # Credit complete files; credit partial bytes so resume progress is accurate
                pending: list[dict] = []
                for sib in siblings:
                    rfilename = sib["rfilename"]
                    file_size = actual_sizes.get(rfilename, 0)
                    file_dest = dest / rfilename
                    existing = file_dest.stat().st_size if file_dest.exists() else 0
                    if file_size > 0 and existing == file_size:
                        downloaded_bytes[rfilename] = file_size   # fully done
                    else:
                        downloaded_bytes[rfilename] = existing    # 0 or partial
                        pending.append(sib)

                async def _download_file(sib: dict) -> None:
                    rfilename = sib["rfilename"]
                    file_dest = dest / rfilename
                    file_dest.parent.mkdir(parents=True, exist_ok=True)
                    hf_url = f"https://huggingface.co/{repo_id}/resolve/main/{rfilename}"
                    resume_from = file_dest.stat().st_size if file_dest.exists() else 0
                    async with sem:
                        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                            headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else {}
                            async with client.stream("GET", hf_url, headers=headers) as resp:
                                if resp.status_code == 416:
                                    return  # server says file already complete
                                if resp.status_code not in (200, 206):
                                    resp.raise_for_status()
                                # 200 means server ignored Range; start fresh
                                if resp.status_code == 200 and resume_from > 0:
                                    resume_from = 0
                                    downloaded_bytes[rfilename] = 0
                                mode = "ab" if resume_from > 0 else "wb"
                                try:
                                    with open(file_dest, mode) as f:
                                        async for chunk in resp.aiter_bytes(1024 * 1024):
                                            f.write(chunk)
                                            downloaded_bytes[rfilename] += len(chunk)
                                            total_done = sum(downloaded_bytes.values())
                                            elapsed = time.monotonic() - t0 or 0.001
                                            speed = total_done / elapsed
                                            pct = (total_done / total_size * 100) if total_size else -1
                                            eta = ((total_size - total_done) / speed) if (total_size and speed > 0) else 0
                                            await progress_queue.put(DownloadProgress(
                                                percent=min(pct, 99.0) if pct >= 0 else -1,
                                                speed_mbps=round(speed / 1e6, 2),
                                                eta_s=round(eta),
                                                total_bytes=total_size,
                                            ))
                                except Exception:
                                    if mode == "wb":
                                        file_dest.unlink(missing_ok=True)
                                    raise

                download_task = asyncio.gather(*[_download_file(s) for s in pending])
                try:
                    while not download_task.done():
                        try:
                            prog = progress_queue.get_nowait()
                            yield prog
                        except asyncio.QueueEmpty:
                            await asyncio.sleep(0.1)

                    await download_task  # re-raises any exception from a worker
                    # Drain any remaining progress events
                    while not progress_queue.empty():
                        yield progress_queue.get_nowait()

                    yield DownloadProgress(100, done=True)
                except BaseException:
                    # Ensure worker coroutines are stopped on cancel, pause, or error —
                    # asyncio.gather tasks are NOT automatically cancelled when the parent is.
                    if not download_task.done():
                        download_task.cancel()
                        try:
                            await download_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    raise

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


async def _resolve_file_sizes(repo_id: str, filenames: list[str]) -> dict[str, int]:
    """HEAD all files in parallel to get accurate Content-Length for each."""
    import httpx

    async def _head(client: httpx.AsyncClient, fn: str) -> tuple[str, int]:
        try:
            r = await client.head(
                f"https://huggingface.co/{repo_id}/resolve/main/{fn}",
                follow_redirects=True,
                timeout=15,
            )
            return fn, int(r.headers.get("content-length", 0))
        except Exception:
            return fn, 0

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_head(client, fn) for fn in filenames])
    return {fn: size for fn, size in results if size > 0}


def _dir_size_gb(path: Path) -> float:
    return _dir_size_bytes(path) / 1_073_741_824


def _dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _extract_param_label(name: str) -> str | None:
    """Extract parameter count from model name, e.g. '7B', '70B', '3.8B'.
    For MoE models (e.g. '30B-A3B') returns only the total param count ('30B').
    """
    import re
    m = re.search(r'(\d+(?:\.\d+)?B)(?:-A\d+(?:\.\d+)?B)?', name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _read_context_length(config_path: Path) -> int | None:
    """Read max context length from an MLX model's config.json."""
    try:
        import json
        cfg = json.loads(config_path.read_text())
        return cfg.get("max_position_embeddings") or cfg.get("max_seq_len") or cfg.get("seq_length")
    except Exception:
        return None


def _has_vision_config(config_path: Path) -> bool:
    """Check if a model's config.json contains vision configuration."""
    try:
        import json
        cfg = json.loads(config_path.read_text())
        return "vision_config" in cfg or "visual" in cfg
    except Exception:
        return False
