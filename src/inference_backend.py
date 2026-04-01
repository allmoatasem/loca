"""
InferenceBackend — manages a local inference server process (mlx_lm or llama.cpp).

Replaces the LM Studio dependency. Detects the right backend from the model
format, starts the server subprocess, polls until healthy, and exposes the
same OpenAI-compatible API at localhost:{port}.

Supported backends:
  - mlx_lm.server  (Apple Silicon only, MLX model directories)
  - llama-server   (cross-platform, GGUF files)
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import sys
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

Backend = Literal["mlx", "llama.cpp"]


class InferenceBackendError(Exception):
    pass


class InferenceBackend:
    def __init__(self, config: dict) -> None:
        inf = config.get("inference", {})
        self.port: int = inf.get("port", 8080)
        self.models_dir: Path = Path(inf.get("models_dir", "~/loca_models")).expanduser()
        self.default_ctx_size: int = inf.get("ctx_size", 8192)
        self.preferred_backend: str = inf.get("backend", "auto")
        self.llama_server_bin: str = inf.get("llama_server", "llama-server")

        self._proc: asyncio.subprocess.Process | None = None
        self._current_model: str | None = None        # display name (basename)
        self._current_model_path: str | None = None   # full path — used as "model" field in API calls
        self._current_backend: Backend | None = None
        self._stderr_lines: list[str] = []   # rolling buffer for error reporting
        self._load_lock = asyncio.Lock()      # prevents concurrent start() calls

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        model_path: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> None:
        """Start the inference server for the given model path."""
        async with self._load_lock:
            await self._start_locked(model_path, ctx_size, n_gpu_layers, batch_size, num_threads)

    async def _start_locked(
        self,
        model_path: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> None:
        """Internal: called only while _load_lock is held."""
        if self._proc and self._proc.returncode is None:
            await self.stop()
        await self._kill_port_squatter()

        ctx = ctx_size or self.default_ctx_size
        backend = self._detect_backend(model_path)
        args = self._build_args(backend, model_path, ctx, n_gpu_layers, batch_size, num_threads)

        logger.info(f"Starting {backend} backend: {' '.join(args)}")
        self._stderr_lines = []
        # KMP_DUPLICATE_LIB_OK suppresses the OpenMP duplicate-library crash that
        # occurs when llama-server or mlx_lm links libomp and another dylib in the
        # same process also links it (common on macOS with Homebrew).
        env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE"}
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._current_model = Path(model_path).name
        self._current_model_path = model_path
        self._current_backend = backend

        # Stream server stderr in background so logs aren't silently swallowed
        asyncio.create_task(self._log_stderr())

        await self._poll_until_ready(timeout=90)
        logger.info(f"Inference backend ready on port {self.port} — model: {self._current_model}")

    async def stop(self) -> None:
        """Gracefully terminate the inference server."""
        if not self._proc:
            return
        if self._proc.returncode is not None:
            self._proc = None
            return
        logger.info("Stopping inference backend…")
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Inference backend did not exit cleanly — killing")
            self._proc.kill()
            await self._proc.wait()
        self._proc = None
        self._current_model = None
        self._current_model_path = None
        self._current_backend = None
        logger.info("Inference backend stopped")

    async def restart(
        self,
        model_path: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> None:
        """Stop then start with a new model."""
        await self.stop()
        await self.start(model_path, ctx_size, n_gpu_layers, batch_size, num_threads)

    async def health_check(self) -> bool:
        """Return True if the backend is responding to /health."""
        if self._proc and self._proc.returncode is not None:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://localhost:{self.port}/health")
                return resp.status_code == 200
        except Exception:
            return False

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def current_model(self) -> str | None:
        """Display name (basename). Used for UI and is_loaded checks."""
        # Clear stale state if the process has exited
        if self._proc is not None and self._proc.returncode is not None:
            self._current_model = None
            self._current_model_path = None
            self._current_backend = None
            self._proc = None
        return self._current_model

    def current_model_path(self) -> str | None:
        """Full filesystem path. Must be used as the 'model' field in mlx_lm API requests."""
        if self._proc is not None and self._proc.returncode is not None:
            self._current_model_path = None
        return self._current_model_path

    def current_backend(self) -> Backend | None:
        return self._current_backend

    def api_base(self) -> str:
        return f"http://localhost:{self.port}"

    # ------------------------------------------------------------------
    # Backend detection
    # ------------------------------------------------------------------

    def _detect_backend(self, model_path: str) -> Backend:
        p = Path(model_path)

        if self.preferred_backend == "mlx":
            return "mlx"
        if self.preferred_backend == "llama.cpp":
            return "llama.cpp"

        # Auto-detect from model format
        if p.suffix.lower() == ".gguf":
            return "llama.cpp"

        if p.is_dir():
            # MLX models are directories containing config.json
            if (p / "config.json").exists():
                if platform.machine() == "arm64" and sys.platform == "darwin":
                    return "mlx"
                raise InferenceBackendError(
                    f"MLX model directories are only supported on Apple Silicon (arm64 macOS). "
                    f"Model: {model_path}. Use a GGUF file for cross-platform support."
                )
            # Directory containing a .gguf file — use llama.cpp
            gguf_files = list(p.glob("*.gguf"))
            if gguf_files:
                return "llama.cpp"
            raise InferenceBackendError(
                f"Cannot detect model format for directory: {model_path}. "
                "Expected either a .gguf file or an MLX model directory with config.json."
            )

        raise InferenceBackendError(
            f"Unrecognised model path: {model_path}. "
            "Provide a .gguf file path or an MLX model directory."
        )

    # ------------------------------------------------------------------
    # Argument builders
    # ------------------------------------------------------------------

    def _build_args(
        self,
        backend: Backend,
        model_path: str,
        ctx_size: int,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> list[str]:
        if backend == "mlx":
            return self._build_mlx_args(model_path, ctx_size)
        return self._build_llama_args(model_path, ctx_size, n_gpu_layers, batch_size, num_threads)

    def _build_mlx_args(self, model_path: str, ctx_size: int) -> list[str]:
        # Prefer the mlx_lm script on PATH (works regardless of which venv is active).
        # Fall back to sys.executable -m mlx_lm if not on PATH.
        # Note: batch_size and num_threads are llama.cpp concepts; mlx_lm handles
        # hardware tuning internally and does not expose these as server flags.
        mlx_bin = shutil.which("mlx_lm")
        if mlx_bin:
            return [
                mlx_bin, "server",
                "--model", model_path,
                "--port", str(self.port),
                "--max-tokens", str(ctx_size),
            ]
        return [
            sys.executable, "-m", "mlx_lm", "server",
            "--model", model_path,
            "--port", str(self.port),
            "--max-tokens", str(ctx_size),
        ]

    def _build_llama_args(
        self,
        model_path: str,
        ctx_size: int,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> list[str]:
        p = Path(model_path)
        # If directory, find the first .gguf inside
        if p.is_dir():
            gguf_files = sorted(p.glob("*.gguf"))
            if not gguf_files:
                raise InferenceBackendError(f"No .gguf file found in directory: {model_path}")
            model_path = str(gguf_files[0])

        args = [
            self.llama_server_bin,
            "-m", model_path,
            "--port", str(self.port),
            "--ctx-size", str(ctx_size),
            "--no-mmap",  # safer for large models — avoids page fault stalls
        ]
        # GPU layer offload — default to full offload on GPU-capable platforms
        if platform.system() in ("Darwin", "Linux"):
            ngl = n_gpu_layers if n_gpu_layers is not None else 99
            args += ["-ngl", str(ngl)]
        if batch_size is not None:
            args += ["-b", str(batch_size)]
        if num_threads is not None:
            args += ["-t", str(num_threads)]

        return args

    # ------------------------------------------------------------------
    # Port management
    # ------------------------------------------------------------------

    async def _kill_port_squatter(self) -> None:
        """Kill any stale process occupying our inference port before starting fresh."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "lsof", "-ti", f":{self.port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            pids = [int(p) for p in stdout.decode().split() if p.strip().isdigit()]
            for pid in pids:
                if pid != os.getpid():
                    logger.info(f"Killing stale process {pid} on port {self.port}")
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            if pids:
                await asyncio.sleep(1.5)  # give OS time to release the port
        except Exception as exc:
            logger.debug(f"_kill_port_squatter: {exc}")

    # ------------------------------------------------------------------
    # Health polling
    # ------------------------------------------------------------------

    async def _poll_until_ready(self, timeout: int = 90) -> None:
        """Poll /health until the server responds or timeout is reached."""
        deadline = asyncio.get_event_loop().time() + timeout
        interval = 1.0
        while asyncio.get_event_loop().time() < deadline:
            if self._proc and self._proc.returncode is not None:
                tail = "\n".join(self._stderr_lines[-20:])
                hint = ""
                if "weight_scale_inv" in tail:
                    hint = " (FP8 models are not yet supported by mlx_lm — use a 4-bit or 8-bit MLX model instead)"
                elif "not found" in tail.lower() or "no such file" in tail.lower():
                    hint = " (model file not found — check the path)"
                raise InferenceBackendError(
                    f"Inference server exited with code {self._proc.returncode}{hint}\n{tail}".strip()
                )
            if await self.health_check():
                return
            await asyncio.sleep(interval)
        raise InferenceBackendError(
            f"Inference backend did not become healthy within {timeout}s"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _log_stderr(self) -> None:
        """Stream server stderr to our logger and keep a rolling buffer for error messages."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").rstrip()
                logger.debug(f"[inference] {decoded}")
                self._stderr_lines.append(decoded)
                # Keep only last 40 lines to bound memory use
                if len(self._stderr_lines) > 40:
                    self._stderr_lines = self._stderr_lines[-40:]
        except Exception:
            pass
