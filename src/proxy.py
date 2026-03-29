"""
FastAPI proxy server.

Intercepts /v1/chat/completions to apply:
  - Intelligent model routing
  - Web search injection
  - Tool call orchestration
  - Memory injection

Manages the local inference backend (mlx_lm or llama-server) directly —
no LM Studio required.

Start with:
    uvicorn src.proxy:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import base64
import io
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import yaml
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .inference_backend import InferenceBackend
from .model_manager import ModelManager
from .orchestrator import Orchestrator
from .store import (
    add_memory,
    delete_conversation,
    delete_memory,
    get_conversation,
    list_conversations,
    list_memories,
    patch_conversation,
    save_conversation,
    search_conversations,
    update_memory,
)

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
_inference_backend: InferenceBackend | None = None
_model_manager: ModelManager | None = None
_orchestrator: Orchestrator | None = None

# In-memory download job tracking: download_id → asyncio.Queue of DownloadProgress
_download_jobs: dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _config, _inference_backend, _model_manager, _orchestrator

    _config = _load_config()
    _inference_backend = InferenceBackend(_config)
    _model_manager = ModelManager(_config, _inference_backend)
    _orchestrator = Orchestrator(_config, _model_manager)

    # Auto-start the active model if configured
    active_rel = _config.get("inference", {}).get("active_model")
    if active_rel:
        active_path = _inference_backend.models_dir / active_rel
        if active_path.exists():
            logger.info(f"Auto-starting inference backend with: {active_path}")
            try:
                await _inference_backend.start(str(active_path))
            except Exception as e:
                logger.warning(f"Could not auto-start inference backend: {e}")

    logger.info("Loca proxy started")
    yield
    # Shutdown
    if _inference_backend:
        await _inference_backend.stop()


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
    # `mode` drives routing/system-prompt; `model` is the actual LLM to call.
    # If only `model` is provided (legacy), treat it as both.
    mode_hint: str | None = body.get("mode") or body.get("model")
    model_override: str | None = body.get("model_override")
    num_ctx: int | None = body.get("num_ctx")
    research_mode: bool = body.get("research_mode", False)

    assert _orchestrator is not None

    if stream:
        return StreamingResponse(
            _openai_stream_response(
                _orchestrator, messages, has_image, mode_hint, model_override, num_ctx, research_mode
            ),
            media_type="text/event-stream",
        )

    response_data = await _orchestrator.handle(
        messages, has_image=has_image, stream=False,
        model_hint=mode_hint, model_override=model_override,
        num_ctx=num_ctx, research_mode=research_mode,
    )
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
    model_override: str | None = None,
    num_ctx: int | None = None,
    research_mode: bool = False,
) -> AsyncIterator[bytes]:
    output_chars = 0
    actual_model = model_override or model_hint or "local"
    search_triggered = False
    memory_injected = False
    try:
        gen = await orchestrator.handle(
            messages, has_image=has_image, stream=True,
            model_hint=model_hint, model_override=model_override,
            num_ctx=num_ctx, research_mode=research_mode,
        )
        async for chunk in gen:
            # Metadata sentinel from orchestrator
            if isinstance(chunk, dict):
                if "__model__" in chunk:
                    actual_model = chunk["__model__"]
                    search_triggered = bool(chunk.get("__search__", False))
                    memory_injected = bool(chunk.get("__memory__", False))
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
            "search_triggered": search_triggered,
            "memory_injected": memory_injected,
        },
    })
    yield f"data: {usage_payload}\n\n".encode()
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# /v1/models — OpenAI-compatible model list
# /api/local-models — full model inventory with format/size info
# /api/models/* — load, delete, download, active status
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def models() -> JSONResponse:
    assert _model_manager is not None
    local = _model_manager.list_local()
    model_list = [{"id": m.name, "object": "model", "owned_by": "local"} for m in local]
    return JSONResponse(content={"object": "list", "data": model_list})


@app.get("/api/local-models")
async def local_models() -> JSONResponse:
    """Return all downloaded models with format, size, and loaded status."""
    assert _model_manager is not None
    return JSONResponse({"models": [m.to_dict() for m in _model_manager.list_local()]})


@app.get("/api/models/active")
async def active_model() -> JSONResponse:
    """Return info about the currently loaded model."""
    assert _inference_backend is not None
    return JSONResponse({
        "name": _inference_backend.current_model(),
        "backend": _inference_backend.current_backend(),
        "api_base": _inference_backend.api_base(),
        "running": _inference_backend.is_running(),
    })


@app.post("/api/models/load")
async def load_model(request: Request) -> JSONResponse:
    """Load a local model into the inference backend."""
    assert _model_manager is not None
    body = await request.json()
    name = body.get("name", "")
    ctx_size = body.get("ctx_size")
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    try:
        model_name, api_base = await _model_manager.load(name, ctx_size)
        return JSONResponse({"ok": True, "name": model_name, "api_base": api_base})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/models/{model_name:path}")
async def delete_model(model_name: str) -> JSONResponse:
    """Delete a downloaded model by name."""
    assert _model_manager is not None
    try:
        _model_manager.delete(model_name)
        return JSONResponse({"ok": True})
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/models/download")
async def start_download(request: Request) -> JSONResponse:
    """
    Start a model download. Returns a download_id to poll for progress.
    Body: {repo_id, filename?, format}
    """
    assert _model_manager is not None
    body = await request.json()
    repo_id = body.get("repo_id", "")
    filename = body.get("filename")
    fmt = body.get("format", "gguf")
    if not repo_id:
        return JSONResponse({"error": "repo_id is required"}, status_code=400)

    download_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _download_jobs[download_id] = queue

    async def _run_download() -> None:
        async for progress in _model_manager.download(repo_id, filename, fmt):
            await queue.put(progress)

    asyncio.create_task(_run_download())
    return JSONResponse({"download_id": download_id})


@app.get("/api/models/download/{download_id}/progress")
async def download_progress(download_id: str) -> StreamingResponse | JSONResponse:
    """SSE stream of DownloadProgress events for a running download."""
    queue = _download_jobs.get(download_id)
    if not queue:
        return JSONResponse({"error": "unknown download_id"}, status_code=404)

    async def _stream() -> AsyncIterator[bytes]:
        while True:
            try:
                progress = await asyncio.wait_for(queue.get(), timeout=30.0)
                data = json.dumps(progress.to_dict())
                yield f"data: {data}\n\n".encode()
                if progress.done or progress.error:
                    _download_jobs.pop(download_id, None)
                    break
            except asyncio.TimeoutError:
                yield b"data: {\"heartbeat\": true}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# /api/upload — process attached files before including them in a message
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> JSONResponse:
    """
    Accept a file upload and return a content descriptor:
      - image/*  → {"type": "image", "data": "data:…;base64,…", "name": …}
      - .pdf     → {"type": "text",  "content": "<extracted text>",  "name": …}
      - audio/*  → {"type": "audio", "name": …}   (transcription not yet supported)
      - video/*  → {"type": "video", "name": …}
      - other    → {"type": "text",  "content": "<utf-8 text>",  "name": …}
    """
    content_type = (file.content_type or "").lower()
    filename = file.filename or "upload"
    data = await file.read()

    if content_type.startswith("image/"):
        b64 = base64.b64encode(data).decode()
        return JSONResponse({
            "type": "image",
            "data": f"data:{content_type};base64,{b64}",
            "name": filename,
        })

    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception as exc:
            text = f"[Could not extract PDF text: {exc}]"
        return JSONResponse({"type": "text", "content": text, "name": filename})

    if content_type.startswith("audio/"):
        return JSONResponse({"type": "audio", "name": filename})

    if content_type.startswith("video/"):
        return JSONResponse({"type": "video", "name": filename})

    # Fallback: try UTF-8 text
    try:
        text = data.decode("utf-8")
        return JSONResponse({"type": "text", "content": text, "name": filename})
    except UnicodeDecodeError:
        return JSONResponse({"type": "binary", "name": filename})


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
    import asyncio
    import os
    import re
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


@app.get("/api/search/conversations")
async def api_search_conversations(q: str = "") -> JSONResponse:
    if not q.strip():
        return JSONResponse({"conversations": []})
    return JSONResponse({"conversations": search_conversations(q)})


@app.patch("/api/conversations/{conv_id}")
async def api_patch_conversation(conv_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    kwargs: dict = {}
    if "starred" in body:
        kwargs["starred"] = bool(body["starred"])
    if "folder" in body:
        kwargs["folder"] = body.get("folder")  # None clears folder
    patch_conversation(conv_id, **kwargs)
    return JSONResponse({"ok": True})


@app.delete("/api/conversations/{conv_id}")
async def api_delete_conversation(conv_id: str) -> JSONResponse:
    delete_conversation(conv_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Memories API
# ---------------------------------------------------------------------------

@app.get("/api/memories")
async def api_list_memories(type: str | None = None) -> JSONResponse:
    return JSONResponse({"memories": list_memories(type=type)})


@app.post("/api/memories")
async def api_add_memory(request: Request) -> JSONResponse:
    body = await request.json()
    mid = add_memory(
        content=body.get("content", ""),
        conv_id=body.get("conv_id"),
        type=body.get("type", "user_fact"),
    )
    return JSONResponse({"id": mid})


@app.patch("/api/memories/{mem_id}")
async def api_update_memory(mem_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    update_memory(mem_id, content)
    return JSONResponse({"ok": True})


@app.delete("/api/memories/{mem_id}")
async def api_delete_memory(mem_id: str) -> JSONResponse:
    delete_memory(mem_id)
    return JSONResponse({"ok": True})


@app.get("/api/hardware")
async def api_hardware() -> JSONResponse:
    """Return a hardware profile for the current machine."""
    from .hardware_profiler import _llmfit_bin, get_hardware_profile
    profile = get_hardware_profile()
    return JSONResponse({
        "platform": profile.platform,
        "arch": profile.arch,
        "cpu_name": profile.cpu_name,
        "total_ram_gb": profile.total_ram_gb,
        "available_ram_gb": profile.available_ram_gb,
        "has_apple_silicon": profile.has_apple_silicon,
        "has_nvidia_gpu": profile.has_nvidia_gpu,
        "supports_mlx": profile.supports_mlx,
        "llmfit_available": bool(_llmfit_bin()),
    })


@app.post("/api/hardware/install-llmfit")
async def api_install_llmfit() -> JSONResponse:
    """Download and install llmfit binary for this platform."""
    import asyncio

    from .hardware_profiler import ensure_llmfit
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(None, ensure_llmfit)
    if path:
        return JSONResponse({"ok": True, "path": path})
    return JSONResponse({"ok": False, "error": "Download failed — check logs"}, status_code=500)


@app.get("/api/recommended-models")
async def api_recommended_models() -> JSONResponse:
    """Return model recommendations from llmfit (or built-in fallback catalog)."""
    from .hardware_profiler import _llmfit_bin, get_hardware_profile, get_recommendations
    profile = get_hardware_profile()
    recs = get_recommendations(profile)
    return JSONResponse({
        "total_ram_gb": profile.total_ram_gb,
        "has_apple_silicon": profile.has_apple_silicon,
        "llmfit_available": bool(_llmfit_bin()),
        "recommendations": [
            {
                "name": r.name,
                "repo_id": r.repo_id,
                "filename": r.filename,
                "format": r.format,
                "size_gb": r.size_gb,
                "quant": r.quant,
                "context": r.context,
                "why": r.why,
            }
            for r in recs
        ],
    })


@app.get("/api/hf-search")
async def api_hf_search(q: str = "", format: str = "gguf", limit: int = 8) -> JSONResponse:
    """Search Hugging Face Hub for models matching query, filtered by format tag."""
    if not q.strip():
        return JSONResponse({"models": []})
    import asyncio

    import httpx as _httpx

    tag = "gguf" if format == "gguf" else "mlx"
    url = "https://huggingface.co/api/models"
    params = {
        "search": q,
        "filter": tag,
        "limit": str(limit),
        "sort": "downloads",
        "direction": "-1",
    }
    try:
        loop = asyncio.get_event_loop()

        def _fetch() -> list[dict]:
            resp = _httpx.get(url, params=params, timeout=8)
            resp.raise_for_status()
            return resp.json()

        results = await loop.run_in_executor(None, _fetch)
        models = [
            {
                "repo_id": m.get("modelId") or m.get("id", ""),
                "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0),
            }
            for m in results
            if isinstance(m, dict)
        ]
        return JSONResponse({"models": models})
    except Exception as e:
        logger.warning(f"HF search failed: {e}")
        return JSONResponse({"models": [], "error": str(e)})


@app.post("/api/extract-memories")
async def api_extract_memories(request: Request) -> JSONResponse:
    """Run three-pass memory extraction on the given messages and persist results."""
    assert _orchestrator is not None
    body = await request.json()
    messages = body.get("messages", [])
    conv_id  = body.get("conv_id")
    saved = await _orchestrator.extract_and_save_memories(messages, conv_id)
    return JSONResponse({"memories": saved})


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
