"""
PluginManager — lifecycle and registry for Loca plugins.

Plugins extend Loca with external processes (e.g. MemPalace, MCP servers)
or built-in Python modules.  Configuration lives in config.yaml:

  plugins:
    memory:
      type: builtin       # builtin | external
      # ── external-only fields ──────────────────────────────────────────
      # command: ["mempalace-server", "--port", "8090"]
      # port: 8090
      # health_path: /health
      # install_cmd: "pip install mempalace"   # run once if command not found

Usage:
    pm = PluginManager(config, inference_backend)
    await pm.start()
    plugin = pm.memory_plugin          # ready to use
    ...
    await pm.stop()
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from typing import TYPE_CHECKING

import httpx

from .plugins.memory_plugin import BuiltinMemoryPlugin, MemoryPlugin

if TYPE_CHECKING:
    from .inference_backend import InferenceBackend

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, config: dict, backend: InferenceBackend) -> None:
        self._config = config.get("plugins", {})
        self._backend = backend
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._memory: MemoryPlugin | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Instantiate / start all configured plugins."""
        mem_cfg = self._config.get("memory", {})
        plugin_type = mem_cfg.get("type", "builtin")

        if plugin_type == "external":
            await self._start_external("memory", mem_cfg)
            # TODO: wire ExternalMemoryPlugin once MemPalace ARM64 issue is resolved
            logger.warning(
                "External memory plugin configured but not yet wired — "
                "falling back to built-in."
            )
            self._memory = BuiltinMemoryPlugin(self._backend)
        else:
            self._memory = BuiltinMemoryPlugin(self._backend)
            logger.info("Memory plugin: built-in (verbatim + semantic retrieval)")

    async def stop(self) -> None:
        """Terminate all plugin subprocesses."""
        for name, proc in self._procs.items():
            if proc.returncode is None:
                logger.info(f"Stopping plugin: {name}")
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
        self._procs.clear()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def memory_plugin(self) -> MemoryPlugin:
        if self._memory is None:
            # Defensive: return a built-in instance if start() wasn't called
            self._memory = BuiltinMemoryPlugin(self._backend)
        return self._memory

    def status(self) -> dict:
        """Return plugin status for /api/plugins endpoint."""
        mem_cfg = self._config.get("memory", {})
        mem_type = mem_cfg.get("type", "builtin")
        mem_running = (
            self._procs["memory"].returncode is None
            if "memory" in self._procs
            else (mem_type == "builtin")  # built-in is always "running"
        )
        return {
            "plugins": [
                {
                    "name": "memory",
                    "type": mem_type,
                    "running": mem_running,
                    "description": (
                        "Verbatim storage + semantic retrieval via local embeddings"
                        if mem_type == "builtin"
                        else f"External plugin on port {mem_cfg.get('port', '?')}"
                    ),
                }
            ]
        }

    # ------------------------------------------------------------------
    # External subprocess helpers
    # ------------------------------------------------------------------

    async def _start_external(self, name: str, cfg: dict) -> None:
        """Start an external plugin subprocess."""
        command: list[str] = cfg.get("command", [])
        if not command:
            logger.warning(f"Plugin '{name}': no command configured, skipping")
            return

        # Auto-install if the binary is missing and install_cmd is set
        bin_name = command[0]
        if not shutil.which(bin_name):
            install_cmd = cfg.get("install_cmd")
            if install_cmd:
                logger.info(f"Plugin '{name}': installing with: {install_cmd}")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install",
                    *install_cmd.removeprefix("pip install ").split(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
            else:
                logger.error(
                    f"Plugin '{name}': command '{bin_name}' not found. "
                    "Set install_cmd in config to auto-install."
                )
                return

        logger.info(f"Starting external plugin '{name}': {' '.join(command)}")
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._procs[name] = proc

        # Wait for health check
        port = cfg.get("port")
        health_path = cfg.get("health_path", "/health")
        if port:
            await self._wait_healthy(name, port, health_path, timeout=30)

    async def _wait_healthy(
        self, name: str, port: int, path: str, timeout: int
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get(f"http://localhost:{port}{path}")
                    if r.status_code == 200:
                        logger.info(f"Plugin '{name}' healthy on port {port}")
                        return
            except Exception:
                pass
            await asyncio.sleep(1.0)
        logger.warning(f"Plugin '{name}' did not become healthy within {timeout}s")
