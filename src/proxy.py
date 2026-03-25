"""
FastAPI proxy server.

Sits between Open WebUI and LM Studio, intercepting /v1/chat/completions to apply:
  - Intelligent model routing
  - Web search injection
  - Tool call orchestration

All other LM Studio endpoints are reverse-proxied transparently.

Start with:
    uvicorn src.proxy:app --host 0.0.0.0 --port 8000 --reload

Point Open WebUI at http://localhost:8000 (OpenAI-compatible mode).
LM Studio runs at localhost:1234.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .model_manager import ModelManager
from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = os.environ.get(
        "ORCHESTRATOR_CONFIG",
        os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
    )
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_config: dict = {}
_model_manager: ModelManager | None = None
_orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _config, _model_manager, _orchestrator

    _config = _load_config()
    proxy_cfg = _config.get("proxy", {})
    lmstudio_url: str = proxy_cfg.get("lmstudio_base_url", "http://localhost:1234")

    _model_manager = ModelManager(_config, lmstudio_base_url=lmstudio_url)
    _orchestrator = Orchestrator(_config, _model_manager)

    logger.info(f"Orchestrator proxy started — forwarding to LM Studio at {lmstudio_url}")
    yield
    # Cleanup (nothing special needed)


app = FastAPI(title="Local AI Orchestrator Proxy", lifespan=lifespan)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


# ---------------------------------------------------------------------------
# /v1/chat/completions — primary intercepted endpoint (OpenAI-compatible)
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def openai_chat(request: Request) -> Response:
    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    stream: bool = body.get("stream", False)
    has_image = _detect_image(messages)
    model_hint: str | None = body.get("model")

    assert _orchestrator is not None

    if stream:
        return StreamingResponse(
            _openai_stream_response(_orchestrator, messages, has_image, model_hint),
            media_type="text/event-stream",
        )

    response_data = await _orchestrator.handle(messages, has_image=has_image, stream=False, model_hint=model_hint)
    # response_data is already an OpenAI-shaped dict from LM Studio — pass it through
    content = ""
    try:
        content = response_data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        pass

    usage = response_data.get("usage") or {}

    return JSONResponse(content={
        "id": response_data.get("id", "chatcmpl-local"),
        "object": "chat.completion",
        "model": response_data.get("model", "local"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    })


async def _openai_stream_response(
    orchestrator: Orchestrator,
    messages: list[dict],
    has_image: bool,
    model_hint: str | None = None,
) -> AsyncIterator[bytes]:
    try:
        gen = await orchestrator.handle(messages, has_image=has_image, stream=True, model_hint=model_hint)
        async for chunk in gen:
            delta = {"role": "assistant", "content": chunk}
            payload = json.dumps({
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "model": "local",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            })
            yield f"data: {payload}\n\n".encode()
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        error_payload = json.dumps({
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "model": "local",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"\n\n[Error: {e}]"}, "finish_reason": "stop"}],
        })
        yield f"data: {error_payload}\n\n".encode()
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# /v1/models — forward to LM Studio (Open WebUI uses this to populate model list)
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def models() -> JSONResponse:
    # Always return our friendly model aliases so Open WebUI's dropdown is clean
    # and model selection maps directly to routing logic.
    models_cfg = _config.get("models", {})
    model_list = [
        {"id": alias, "object": "model", "owned_by": "local"}
        for alias in models_cfg
    ]
    return JSONResponse(content={"object": "list", "data": model_list})


# ---------------------------------------------------------------------------
# UI — serve the chat interface
# ---------------------------------------------------------------------------

_STATIC = os.path.join(os.path.dirname(__file__), "static")

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC, "index.html"))

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Transparent reverse proxy for all other LM Studio endpoints
# ---------------------------------------------------------------------------

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def reverse_proxy(request: Request, path: str) -> Response:
    lmstudio_url = _config.get("proxy", {}).get("lmstudio_base_url", "http://localhost:1234")
    target_url = f"{lmstudio_url}/{path}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        proxied = await client.request(
            method=request.method,
            url=target_url,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
            params=dict(request.query_params),
        )

    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=dict(proxied.headers),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_image(messages: list[dict]) -> bool:
    """Check if the last user message contains an image."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                return any(
                    p.get("type") in ("image_url", "image") for p in content if isinstance(p, dict)
                )
            # Check for base64 image data
            if isinstance(content, str) and "data:image/" in content:
                return True
    return False
