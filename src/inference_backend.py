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
        self._current_model: str | None = None
        self._current_backend: Backend | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, model_path: str, ctx_size: int | None = None) -> None:
        """Start the inference server for the given model path."""
        if self._proc and self._proc.returncode is None:
            await self.stop()

        ctx = ctx_size or self.default_ctx_size
        backend = self._detect_backend(model_path)
        args = self._build_args(backend, model_path, ctx)

        logger.info(f"Starting {backend} backend: {' '.join(args)}")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._current_model = Path(model_path).name
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
        self._current_backend = None
        logger.info("Inference backend stopped")

    async def restart(self, model_path: str, ctx_size: int | None = None) -> None:
        """Stop then start with a new model."""
        await self.stop()
        await self.start(model_path, ctx_size)

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
        return self._current_model

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

    def _build_args(self, backend: Backend, model_path: str, ctx_size: int) -> list[str]:
        if backend == "mlx":
            return self._build_mlx_args(model_path, ctx_size)
        return self._build_llama_args(model_path, ctx_size)

    def _build_mlx_args(self, model_path: str, ctx_size: int) -> list[str]:
        return [
            sys.executable, "-m", "mlx_lm.server",
            "--model", model_path,
            "--port", str(self.port),
            "--max-tokens", str(ctx_size),
        ]

    def _build_llama_args(self, model_path: str, ctx_size: int) -> list[str]:
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
        # Offload all layers to GPU if available
        if platform.system() in ("Darwin", "Linux"):
            args += ["-ngl", "99"]

        return args

    # ------------------------------------------------------------------
    # Health polling
    # ------------------------------------------------------------------

    async def _poll_until_ready(self, timeout: int = 90) -> None:
        """Poll /health until the server responds or timeout is reached."""
        deadline = asyncio.get_event_loop().time() + timeout
        interval = 1.0
        while asyncio.get_event_loop().time() < deadline:
            if self._proc and self._proc.returncode is not None:
                raise InferenceBackendError(
                    f"Inference server exited unexpectedly with code {self._proc.returncode}"
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
        """Stream server stderr to our logger so it's not silently dropped."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.debug(f"[inference] {line.decode(errors='replace').rstrip()}")
        except Exception:
            pass
