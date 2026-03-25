"""
Model manager — tracks which models are active and enforces the loading strategy:
  - `general` stays loaded at all times
  - Only one specialist loaded alongside general
  - Specialists unload after idle_unload_minutes of inactivity

Supports both LM Studio (default) and Ollama backends — each model entry in
config.yaml may carry an optional `api_base` field that overrides the default
LM Studio URL.  model_sync.py sets this automatically for Ollama models.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from .router import Model

logger = logging.getLogger(__name__)


class ModelManager:
    def __init__(self, config: dict, lmstudio_base_url: str = "http://localhost:1234"):
        self.config = config
        self.base_url = lmstudio_base_url.rstrip("/")
        self._model_cfg: dict = config["models"]

        self._last_used: dict[str, float] = {}
        self._loaded_specialist: Optional[Model] = None
        self._idle_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def ensure_loaded(self, model: Model) -> tuple[str, str]:
        """
        Ensure the model is active and return (lmstudio_name, api_base).
        For specialists, evicts any conflicting specialist from state tracking first.
        """
        cfg = self._model_cfg[model.value]
        model_name: str = cfg["lmstudio_name"]
        api_base: str = cfg.get("api_base", self.base_url)

        if model == Model.GENERAL:
            self._last_used[model.value] = time.time()
            return model_name, api_base

        # Specialist: evict any different specialist from state tracking
        if self._loaded_specialist and self._loaded_specialist != model:
            logger.info(
                f"Swapping specialist: {self._loaded_specialist.value} → {model.value} "
                f"(backend will release {self._loaded_specialist.value} on next model call)"
            )
            self._loaded_specialist = None

        if self._loaded_specialist != model:
            logger.info(f"Loading specialist: {model.value} ({model_name})")
            await self._warmup(model_name, api_base)
            self._loaded_specialist = model

        self._last_used[model.value] = time.time()
        self._schedule_idle_check()
        return model_name, api_base

    async def get_model_name(self, model: Model) -> str:
        return self._model_cfg[model.value]["lmstudio_name"]

    async def get_model_api_base(self, model: Model) -> str:
        return self._model_cfg[model.value].get("api_base", self.base_url)

    async def list_loaded(self) -> list[str]:
        """Return model IDs currently reported by LM Studio's /v1/models."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{self.base_url}/v1/models")
                resp.raise_for_status()
                data = resp.json()
                return [m["id"] for m in data.get("data", [])]
            except httpx.HTTPError as e:
                logger.warning(f"Could not fetch model list from LM Studio: {e}")
                return []

    # ------------------------------------------------------------------
    # Warmup (load trigger)
    # ------------------------------------------------------------------

    async def _warmup(self, model_name: str, api_base: str | None = None) -> None:
        """
        Send a minimal chat completion to trigger the backend to load the model.
        Non-fatal — the real request will still attempt the load if this fails.
        """
        base = (api_base or self.base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                await client.post(
                    f"{base}/v1/chat/completions",
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                )
                logger.info(f"Warmup complete: {model_name}")
            except httpx.HTTPError as e:
                logger.warning(f"Warmup request failed for {model_name}: {e}")

    # ------------------------------------------------------------------
    # Idle timeout management (state tracking)
    # ------------------------------------------------------------------

    def _schedule_idle_check(self) -> None:
        if self._idle_task and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_watcher())

    async def _idle_watcher(self) -> None:
        while self._loaded_specialist is not None:
            await asyncio.sleep(60)
            specialist = self._loaded_specialist
            if specialist is None:
                break
            cfg = self._model_cfg[specialist.value]
            idle_minutes: Optional[int] = cfg.get("idle_unload_minutes")
            if idle_minutes is None:
                break

            last = self._last_used.get(specialist.value, 0)
            idle_for = (time.time() - last) / 60
            if idle_for >= idle_minutes:
                logger.info(
                    f"Specialist {specialist.value} idle for {idle_for:.1f}m — "
                    f"marking unloaded (backend will release RAM on next model swap)"
                )
                self._loaded_specialist = None
                break
