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

import asyncio
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
from .store import (
    list_conversations, get_conversation, save_conversation, delete_conversation,
    list_memories, add_memory, delete_memory,
)
from .memory_extractor import extract_memories

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
    num_ctx: int | None = body.get("num_ctx")
    research_mode: bool = body.get("research_mode", False)

    assert _orchestrator is not None

    if stream:
        return StreamingResponse(
            _openai_stream_response(_orchestrator, messages, has_image, model_hint, num_ctx, research_mode),
            media_type="text/event-stream",
        )

    response_data = await _orchestrator.handle(messages, has_image=has_image, stream=False, model_hint=model_hint, num_ctx=num_ctx, research_mode=research_mode)
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
    num_ctx: int | None = None,
    research_mode: bool = False,
) -> AsyncIterator[bytes]:
    output_chars = 0
    actual_model = model_hint or "local"
    try:
        gen = await orchestrator.handle(messages, has_image=has_image, stream=True, model_hint=model_hint, num_ctx=num_ctx, research_mode=research_mode)
        async for chunk in gen:
            # Metadata sentinel from orchestrator — grab actual model name
            if isinstance(chunk, dict):
                if "__model__" in chunk:
                    actual_model = chunk["__model__"]
                continue
            output_chars += len(chunk)
            delta = {"role": "assistant", "content": chunk}
            payload = json.dumps({
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "model": actual_model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            })
            yield f"data: {payload}\n\n".encode()
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        error_payload = json.dumps({
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "model": actual_model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"\n\n[Error: {e}]"}, "finish_reason": "stop"}],
        })
        yield f"data: {error_payload}\n\n".encode()
    # Emit usage summary before DONE so the UI can show token stats + actual model name
    completion_tokens = max(1, output_chars // 4)
    prompt_tokens = sum(len(m.get("content", "") if isinstance(m.get("content"), str) else "") for m in messages) // 4
    usage_payload = json.dumps({
        "id": "chatcmpl-local",
        "object": "chat.completion.chunk",
        "model": actual_model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    })
    yield f"data: {usage_payload}\n\n".encode()
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
    return FileResponse(
        os.path.join(_STATIC, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.get("/assets/{file_path:path}")
async def serve_asset(file_path: str) -> Response:
    """Serve static assets (JS, CSS) bundled with the app."""
    full = os.path.join(_STATIC, "assets", file_path)
    if os.path.isfile(full):
        return FileResponse(full)
    return Response(status_code=404)


@app.get("/system-stats")
async def system_stats() -> JSONResponse:
    import asyncio, re, os
    try:
        proc = await asyncio.create_subprocess_exec(
            "vm_stat",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        text = out.decode()
        page = 4096

        def pages(key: str) -> int:
            m = re.search(rf"{key}:\s+(\d+)", text)
            return int(m.group(1)) * page if m else 0

        active     = pages("Pages active")
        wired      = pages("Pages wired down")
        compressed = pages("Pages occupied by compressor")
        used  = active + wired + compressed
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        GiB   = 1_073_741_824
        return JSONResponse({
            "ram_used_gb":  round(used  / GiB, 1),
            "ram_total_gb": round(total / GiB, 1),
        })
    except Exception:
        return JSONResponse({"ram_used_gb": None, "ram_total_gb": None})


# ---------------------------------------------------------------------------
# Conversations API
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
async def api_list_conversations() -> JSONResponse:
    return JSONResponse({"conversations": list_conversations()})


@app.get("/api/conversations/{conv_id}")
async def api_get_conversation(conv_id: str) -> JSONResponse:
    conv = get_conversation(conv_id)
    if not conv:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(conv)


@app.post("/api/conversations")
async def api_save_conversation(request: Request) -> JSONResponse:
    body = await request.json()
    cid = save_conversation(
        conv_id=body.get("id"),
        title=body.get("title", "Untitled"),
        messages=body.get("messages", []),
        model=body.get("model", ""),
    )
    return JSONResponse({"id": cid})


@app.delete("/api/conversations/{conv_id}")
async def api_delete_conversation(conv_id: str) -> JSONResponse:
    delete_conversation(conv_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Memories API
# ---------------------------------------------------------------------------

@app.get("/api/memories")
async def api_list_memories() -> JSONResponse:
    return JSONResponse({"memories": list_memories()})


@app.post("/api/memories")
async def api_add_memory(request: Request) -> JSONResponse:
    body = await request.json()
    mid = add_memory(content=body.get("content", ""), conv_id=body.get("conv_id"))
    return JSONResponse({"id": mid})


@app.delete("/api/memories/{mem_id}")
async def api_delete_memory(mem_id: str) -> JSONResponse:
    delete_memory(mem_id)
    return JSONResponse({"ok": True})


@app.post("/api/extract-memories")
async def api_extract_memories(request: Request) -> JSONResponse:
    """Run memory extraction on the given messages and persist new facts."""
    assert _orchestrator is not None
    body = await request.json()
    messages = body.get("messages", [])
    conv_id  = body.get("conv_id")
    saved = await _orchestrator.extract_and_save_memories(messages, conv_id)
    return JSONResponse({"memories": saved})


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
