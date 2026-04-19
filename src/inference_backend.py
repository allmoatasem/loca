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
import re
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


_llama_version_cache: dict[str, int] = {}   # keyed by bin path


class InferenceBackend:
    def __init__(self, config: dict) -> None:
        inf = config.get("inference", {})
        self.port: int = inf.get("port", 8080)
        self.models_dir: Path = Path(inf.get("models_dir", "~/loca_models")).expanduser()
        self.default_ctx_size: int = inf.get("ctx_size", 8192)
        self.preferred_backend: str = inf.get("backend", "auto")
        self.llama_server_bin: str = inf.get("llama_server", "llama-server")

        # External server mode (LM Studio / Ollama / custom) — skips subprocess management
        # Accepts both new key (external_server) and old key (lm_studio) for backward compat
        self.lm_studio_mode: bool = bool(
            inf.get("external_server", inf.get("lm_studio", False))
        )
        self.lm_studio_url: str = str(
            inf.get("external_server_url", inf.get("lm_studio_url", "http://localhost:1234"))
        )

        self._proc: asyncio.subprocess.Process | None = None
        self._current_model: str | None = None        # display name (basename)
        self._current_model_path: str | None = None   # full path — used as "model" field in API calls
        self._current_backend: Backend | None = None
        self._current_adapter_path: str | None = None  # None when no LoRA adapter active
        self._stderr_lines: list[str] = []   # rolling buffer for error reporting
        self._load_lock = asyncio.Lock()      # prevents concurrent start() calls
        self._llama_outdated: bool | None = None   # None = not checked yet

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
        adapter_path: str | None = None,
    ) -> None:
        """Start the inference server for the given model path."""
        if self.lm_studio_mode:
            logger.info("LM Studio mode — skipping local backend start")
            return
        async with self._load_lock:
            await self._start_locked(
                model_path, ctx_size, n_gpu_layers, batch_size, num_threads, adapter_path,
            )

    async def _start_locked(
        self,
        model_path: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
        adapter_path: str | None = None,
    ) -> None:
        """Internal: called only while _load_lock is held."""
        if self._proc and self._proc.returncode is None:
            await self.stop()
        await self._kill_port_squatter()

        ctx = ctx_size or self.default_ctx_size
        backend = self._detect_backend(model_path)
        args = self._build_args(
            backend, model_path, ctx, n_gpu_layers, batch_size, num_threads, adapter_path,
        )

        # Check llama.cpp version once per session (non-blocking: fire and cache).
        if backend == "llama.cpp" and self._llama_outdated is None:
            self._llama_outdated = await self.is_llama_outdated(self.llama_server_bin)
            build = await self.get_llama_build(self.llama_server_bin)
            if self._llama_outdated:
                logger.warning(
                    f"llama-server build {build} is outdated — a newer version is available. "
                    "Run: brew upgrade llama.cpp"
                )
            elif build:
                logger.info(f"llama-server build {build} is up to date")

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
        self._current_adapter_path = adapter_path

        # Stream server stderr in background so logs aren't silently swallowed
        asyncio.create_task(self._log_stderr())

        await self._poll_until_ready(timeout=90)
        logger.info(f"Inference backend ready on port {self.port} — model: {self._current_model}")

    async def stop(self) -> None:
        """Gracefully terminate the inference server."""
        if self.lm_studio_mode:
            return
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
        self._current_adapter_path = None
        logger.info("Inference backend stopped")

    async def restart(
        self,
        model_path: str,
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
        adapter_path: str | None = None,
    ) -> None:
        """Stop then start with a new model."""
        await self.stop()
        await self.start(
            model_path, ctx_size, n_gpu_layers, batch_size, num_threads, adapter_path,
        )

    async def health_check(self) -> bool:
        """Return True if the backend is responding."""
        if self.lm_studio_mode:
            # Try common health endpoints — LM Studio: /health, Ollama: root /
            base = self.lm_studio_url.rstrip("/")
            for path in ("/health", "/api/health", "/"):
                try:
                    async with httpx.AsyncClient(timeout=3.0) as client:
                        resp = await client.get(f"{base}{path}")
                        if resp.status_code == 200:
                            return True
                except Exception:
                    continue
            return False
        if self._proc and self._proc.returncode is not None:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://localhost:{self.port}/health")
                return resp.status_code == 200
        except Exception:
            return False

    def is_running(self) -> bool:
        if self.lm_studio_mode:
            return True  # assume LM Studio is running; health_check will verify
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

    def current_adapter_path(self) -> str | None:
        """Absolute path of the LoRA adapter currently active, or None."""
        if self._proc is not None and self._proc.returncode is not None:
            self._current_adapter_path = None
        return self._current_adapter_path

    def api_base(self) -> str:
        if self.lm_studio_mode:
            return self.lm_studio_url
        return f"http://localhost:{self.port}"

    # ------------------------------------------------------------------
    # llama.cpp version helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def get_llama_build(bin_path: str = "llama-server") -> int | None:
        """Return the llama-server build number, or None if not parseable.

        Parses the line:  version: 8110 (237958db3)
        Result is cached per binary path for the lifetime of the process.
        """
        cached = _llama_version_cache.get(bin_path)
        if cached is not None:
            return cached
        try:
            proc = await asyncio.create_subprocess_exec(
                bin_path, "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            m = re.search(r"version:\s*(\d+)", stderr.decode(errors="replace"))
            if m:
                build = int(m.group(1))
                _llama_version_cache[bin_path] = build
                return build
        except Exception as exc:
            logger.debug(f"get_llama_build: {exc}")
        return None

    @staticmethod
    async def is_llama_outdated(bin_path: str = "llama-server") -> bool:
        """Return True if brew reports a newer llama.cpp version is available.

        Runs ``brew outdated --quiet llama.cpp``.  Returns False on any error
        (no Homebrew, no network, etc.) so it never blocks the load path.
        """
        try:
            brew = shutil.which("brew")
            if not brew:
                return False
            proc = await asyncio.create_subprocess_exec(
                brew, "outdated", "--quiet", "llama.cpp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            return bool(stdout.strip())
        except Exception as exc:
            logger.debug(f"is_llama_outdated: {exc}")
            return False

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
        adapter_path: str | None = None,
    ) -> list[str]:
        if backend == "mlx":
            return self._build_mlx_args(model_path, ctx_size, adapter_path)
        # llama.cpp LoRA loading is deferred until the GGUF fine-tuning path
        # lands — for now we silently ignore adapter_path for this backend
        # rather than error out, because the UI guards adapter activation
        # behind the MLX model picker.
        return self._build_llama_args(model_path, ctx_size, n_gpu_layers, batch_size, num_threads)

    def _build_mlx_args(
        self, model_path: str, ctx_size: int, adapter_path: str | None = None,
    ) -> list[str]:
        # Use mlx-vlm for vision models, mlx_lm for text-only.
        # Detected from config.json: presence of "vision_config" or "visual" key.
        module = "mlx_lm"
        if self._model_has_vision(model_path):
            module = "mlx_vlm"
            logger.info(f"Vision model detected — using {module} backend")

        mlx_bin = shutil.which(module)
        if mlx_bin:
            args = [mlx_bin, "server", "--model", model_path, "--port", str(self.port)]
        else:
            args = [sys.executable, "-m", module, "server", "--model", model_path, "--port", str(self.port)]

        # --max-tokens is mlx_lm only; mlx_vlm uses --max-kv-size
        if module == "mlx_lm":
            args += ["--max-tokens", str(ctx_size)]

        # LoRA adapter — only threaded for mlx_lm. mlx_vlm doesn't currently
        # accept `--adapter-path` in its server CLI, so we skip silently
        # there rather than pass an unknown flag that would fail startup.
        if adapter_path and module == "mlx_lm":
            args += ["--adapter-path", adapter_path]

        return args

    @staticmethod
    def _model_has_vision(model_path: str) -> bool:
        """Check if an MLX model has vision support via config.json."""
        import json
        try:
            cfg = json.loads((Path(model_path) / "config.json").read_text())
            return "vision_config" in cfg or "visual" in cfg
        except Exception:
            return False

    def _build_llama_args(
        self,
        model_path: str,
        ctx_size: int,
        n_gpu_layers: int | None = None,
        batch_size: int | None = None,
        num_threads: int | None = None,
    ) -> list[str]:
        p = Path(model_path)
        mmproj_path: str | None = None

        # If directory, find the main .gguf and any mmproj file
        if p.is_dir():
            gguf_files = sorted(p.glob("*.gguf"))
            if not gguf_files:
                raise InferenceBackendError(f"No .gguf file found in directory: {model_path}")
            # Separate mmproj from main model files
            mmproj_candidates = [f for f in gguf_files if "mmproj" in f.name.lower()]
            main_candidates = [f for f in gguf_files if "mmproj" not in f.name.lower()]
            if not main_candidates:
                raise InferenceBackendError(f"No main model .gguf found in directory: {model_path}")
            model_path = str(main_candidates[0])
            if mmproj_candidates:
                mmproj_path = str(mmproj_candidates[0])
        else:
            # Single .gguf file — check for mmproj sibling in same directory
            sibling_mmproj = list(p.parent.glob("*mmproj*.gguf"))
            if sibling_mmproj:
                mmproj_path = str(sibling_mmproj[0])

        args = [
            self.llama_server_bin,
            "-m", model_path,
            "--port", str(self.port),
            "--ctx-size", str(ctx_size),
            "--no-mmap",  # safer for large models — avoids page fault stalls
        ]
        # Vision: multi-modal projector for GGUF vision models
        if mmproj_path:
            args += ["--mmproj", mmproj_path]
            logger.info(f"GGUF vision model detected — using mmproj: {mmproj_path}")
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
                elif "unknown model architecture" in tail.lower():
                    arch_match = re.search(r"unknown model architecture: ['\"]?(\w+)['\"]?", tail, re.IGNORECASE)
                    arch = f" '{arch_match.group(1)}'" if arch_match else ""
                    upgrade_hint = " Run: brew upgrade llama.cpp"
                    if self._llama_outdated:
                        hint = f" (llama-server does not support the{arch} architecture — your llama.cpp is outdated.{upgrade_hint})"
                    else:
                        hint = f" (llama-server does not support the{arch} architecture — your llama.cpp may need updating.{upgrade_hint})"
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
