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
import collections
import io
import json
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import yaml
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .inference_backend import InferenceBackend
from .model_manager import ModelManager
from .orchestrator import Orchestrator
from .plugin_manager import PluginManager
from .provenance import (
    Provenance,
    RetrievedMemory,
    append_verifier_footer,
    verify_citations,
    write_provenance,
)
from .store import (
    add_project_item,
    count_project_items,
    create_project,
    create_project_watch,
    delete_conversation,
    delete_project,
    delete_project_item,
    delete_project_watch,
    get_conversation,
    get_project,
    list_conversations,
    list_import_history,
    list_project_conversations,
    list_project_items,
    list_project_watches,
    list_projects,
    list_vault_notes,
    list_vault_paths,
    patch_conversation,
    patch_project,
    save_conversation,
    search_conversations,
    set_conversation_project,
)
from .voice_backend import VoiceBackend

logger = logging.getLogger(__name__)


def _basename(model_id: str) -> str:
    """Strip filesystem path from model ID — mlx_lm returns the full path."""
    return os.path.basename(model_id.rstrip("/")) if "/" in model_id else model_id


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
_voice_backend: VoiceBackend | None = None
_plugin_manager: PluginManager | None = None

# In-memory download job tracking
_download_jobs:  dict[str, asyncio.Queue]  = {}   # download_id → progress queue
_download_tasks: dict[str, asyncio.Task]   = {}   # download_id → running asyncio Task
_download_meta:  dict[str, dict]           = {}   # download_id → {repo_id, filename, format}
_recs_cache:      dict | None               = None  # cached /api/recommended-models response
_recs_cache_lock: asyncio.Lock | None      = None  # ensures only one build runs at a time


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _config, _inference_backend, _model_manager, _orchestrator, _voice_backend, _plugin_manager

    _config = _load_config()
    _inference_backend = InferenceBackend(_config)
    _model_manager = ModelManager(_config, _inference_backend)
    _voice_backend = VoiceBackend(_config)
    _plugin_manager = PluginManager(_config, _inference_backend)
    await _plugin_manager.start()
    _orchestrator = Orchestrator(
        _config, _model_manager,
        voice_backend=_voice_backend,
        memory_plugin=_plugin_manager.memory_plugin,
    )

    logger.info("Loca proxy started")
    asyncio.create_task(_build_recs_cache())
    # Extracted into src.watches_runner so the executor is unit-testable
    # without a live FastAPI app. See that module for tick cadence, per-
    # watch timeout, and the single-shot agent design.
    from .watches_runner import watches_loop  # noqa: PLC0415
    watches_task = asyncio.create_task(watches_loop())
    # Obsidian Watcher — app-level background vault sync. Replaces the
    # per-project Sync Vault flow; see `src/obsidian_watcher.py`.
    from .obsidian_watcher import watcher_loop as _obsidian_loop  # noqa: PLC0415
    obsidian_task = asyncio.create_task(_obsidian_loop())
    yield
    # Shutdown
    watches_task.cancel()
    obsidian_task.cancel()
    if _plugin_manager:
        await _plugin_manager.stop()
    if _inference_backend:
        await _inference_backend.stop()


app = FastAPI(title="Local AI Orchestrator Proxy", lifespan=lifespan)

# Tightened CORS: Loca is local-first, so only localhost origins are allowed.
# Non-browser callers (Swift app, curl, agentic-coding clients) don't trigger
# CORS, so this doesn't constrain them. The `*_regex` handles arbitrary ports
# the user might run the browser UI or dev server on.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate-limit buckets: {(client_ip, (method, path)): deque[timestamp]}
# In-memory only. Resets on server restart. Sufficient for Loca's local-first
# threat model (protects against accidental runaway scripts + HF abuse).
_RATE_LIMITS: dict[tuple[str, str], tuple[int, int]] = {
    # (method, exact-path): (max_requests, window_seconds)
    ("POST", "/api/upload"): (30, 60),
    ("POST", "/api/models/download"): (10, 60),
    ("GET", "/api/hf-search"): (60, 60),
}
_RATE_LIMIT_BUCKETS: dict[tuple[str, tuple[str, str]], collections.deque[float]] = {}


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    key = (request.method, request.url.path)
    rule = _RATE_LIMITS.get(key)
    if rule is None:
        return await call_next(request)
    limit, window = rule
    ip = request.client.host if request.client else "unknown"
    bucket_key = (ip, key)
    now = time.monotonic()
    bucket = _RATE_LIMIT_BUCKETS.setdefault(bucket_key, collections.deque())
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        return JSONResponse(
            status_code=429,
            content={"error": {
                "message": f"Rate limit exceeded ({limit} req / {window}s) for {key[0]} {key[1]}",
                "type": "rate_limit_exceeded",
            }},
            headers={"Retry-After": str(window)},
        )
    bucket.append(now)
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    return response

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
    # Deep Dive = autonomous loop (Researcher → Writer → Verifier) +
    # Playwright full-page fetch for every sub-agent web search.
    # Consolidated in omnibus #92; the previous split between this flag
    # and `autonomous_loop` confused users without adding any value
    # (they always wanted both together).
    research_mode: bool = body.get("research_mode", False)
    # Conversation id — the loop uses it to name its plan checkpoint so
    # an interrupted session leaves a readable trail on disk.
    conv_id: str | None = body.get("conv_id")
    # Research Partner inputs — partner_mode swaps the layered system
    # prompt (critique / teach), project_id scopes retrieval. Both are
    # opt-in; absent means default behaviour.
    partner_mode: str | None = body.get("partner_mode")
    project_id: str | None = body.get("project_id")
    temperature: float | None = body.get("temperature")
    top_p: float | None = body.get("top_p")
    top_k: int | None = body.get("top_k")
    repeat_penalty: float | None = body.get("repeat_penalty")
    max_tokens: int | None = body.get("max_tokens")
    system_prompt_override: str | None = body.get("system_prompt_override") or None
    # OpenAI function-calling — agentic coding clients (claw-code, Aider, Continue)
    # send these; when present we skip Loca's custom tool loop and forward verbatim.
    tools: list[dict] | None = body.get("tools")
    tool_choice = body.get("tool_choice")
    # Jinja chat-template kwargs (e.g. Qwen3's enable_thinking / Qwen3.6's
    # preserve_thinking) and arbitrary extras (min_p, mirostat_*, xtc_*,
    # dry_*, grammar, …). Both are forwarded as-is to the backend.
    chat_template_kwargs: dict | None = body.get("chat_template_kwargs")
    extra_body: dict | None = body.get("extra_body")

    assert _orchestrator is not None

    if tools:
        if stream:
            return StreamingResponse(
                cast(AsyncIterator[bytes], await _orchestrator.handle_passthrough(
                    messages, tools=tools, tool_choice=tool_choice,
                    has_image=has_image, stream=True,
                    model_hint=mode_hint, model_override=model_override,
                    num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k,
                    repeat_penalty=repeat_penalty, max_tokens=max_tokens,
                    system_prompt_override=system_prompt_override,
                    chat_template_kwargs=chat_template_kwargs,
                    extra_body=extra_body,
                )),
                media_type="text/event-stream",
            )
        passthrough_response = cast(dict, await _orchestrator.handle_passthrough(
            messages, tools=tools, tool_choice=tool_choice,
            has_image=has_image, stream=False,
            model_hint=mode_hint, model_override=model_override,
            num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            system_prompt_override=system_prompt_override,
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
        ))
        return JSONResponse(content=passthrough_response)

    if stream:
        return StreamingResponse(
            _openai_stream_response(
                _orchestrator, messages, has_image, mode_hint, model_override, num_ctx, research_mode,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
                system_prompt_override=system_prompt_override,
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
                partner_mode=partner_mode, project_id=project_id,
                conv_id=conv_id,
            ),
            media_type="text/event-stream",
        )

    response_data = cast(dict, await _orchestrator.handle(
        messages, has_image=has_image, stream=False,
        model_hint=mode_hint, model_override=model_override,
        num_ctx=num_ctx, research_mode=research_mode,
        conv_id=conv_id,
        partner_mode=partner_mode, project_id=project_id,
        temperature=temperature, top_p=top_p, top_k=top_k,
        repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        system_prompt_override=system_prompt_override,
        chat_template_kwargs=chat_template_kwargs,
        extra_body=extra_body,
    ))
    # response_data is already an OpenAI-shaped dict from LM Studio — pass it through
    content = ""
    try:
        content = response_data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        pass

    usage = response_data.get("usage") or {}

    # Store the exchange verbatim in memory (fire-and-forget)
    if content:
        full_messages = messages + [{"role": "assistant", "content": content}]
        asyncio.create_task(_orchestrator.extract_and_save_memories(full_messages))

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
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    repeat_penalty: float | None = None,
    max_tokens: int | None = None,
    system_prompt_override: str | None = None,
    chat_template_kwargs: dict | None = None,
    extra_body: dict | None = None,
    partner_mode: str | None = None,
    project_id: str | None = None,
    conv_id: str | None = None,
) -> AsyncIterator[bytes]:
    output_chars = 0
    actual_model = model_override or model_hint or "local"
    search_triggered = False
    memory_injected = False
    provenance_seed: dict = {}
    reply_chunks: list[str] = []
    try:
        gen = cast(AsyncIterator[str | dict], await orchestrator.handle(
            messages, has_image=has_image, stream=True,
            model_hint=model_hint, model_override=model_override,
            num_ctx=num_ctx, research_mode=research_mode,
            conv_id=conv_id,
            partner_mode=partner_mode, project_id=project_id,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            system_prompt_override=system_prompt_override,
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
        ))
        async for chunk in gen:
            # Metadata sentinel from orchestrator
            if isinstance(chunk, dict):
                if "__model__" in chunk:
                    actual_model = _basename(chunk["__model__"])
                    search_triggered = bool(chunk.get("__search__", False))
                    memory_injected = bool(chunk.get("__memory__", False))
                    provenance_seed = dict(chunk.get("__provenance__", {}))
                continue
            output_chars += len(chunk)
            reply_chunks.append(chunk)
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
    # Per-turn citation map so the client can deep-link [memory: N]
    # clicks to the actual memory record. List is ordered by index —
    # position 0 → [memory: 1]. Empty when no memory was injected so
    # the client knows this turn has no resolvable citations.
    citation_ids: list[str] = []
    if memory_injected:
        for m in provenance_seed.get("retrieved", []):
            mid = str(m.get("id") or "")
            if mid:
                citation_ids.append(mid)
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
            "citation_ids": citation_ids,
        },
    })
    # Verifier pass: parse [memory: N] from the completed answer, flag any
    # N outside the retrieved set, append a footnote paragraph as another
    # SSE delta so it renders inline in the existing chat bubble. Runs only
    # if memory was injected (otherwise there's nothing to verify against).
    full_reply = "".join(reply_chunks)
    retrieved_raw = provenance_seed.get("retrieved", []) if memory_injected else []
    phantoms: list[int] = []
    if retrieved_raw and full_reply:
        phantoms = verify_citations(full_reply, retrieved_count=len(retrieved_raw))
        if phantoms:
            footer = append_verifier_footer("", phantoms=phantoms)
            footer_payload = json.dumps({
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "model": actual_model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": footer}, "finish_reason": None}],
            })
            yield f"data: {footer_payload}\n\n".encode()

    yield f"data: {usage_payload}\n\n".encode()
    yield b"data: [DONE]\n\n"
    # Store the exchange verbatim in memory (fire-and-forget, after stream is done)
    if full_reply:
        full_messages = messages + [{"role": "assistant", "content": full_reply}]
        asyncio.create_task(orchestrator.extract_and_save_memories(full_messages))

    # Provenance sidecar — fire-and-forget. Never blocks the response.
    # Also written when retrieval was skipped due to a meta-query, so the
    # audit trail captures the "no-recall" turns for later tuning.
    should_log_prov = bool(full_reply) and (
        bool(retrieved_raw) or provenance_seed.get("skipped_meta_query")
    )
    if should_log_prov:
        try:
            retrieved = [RetrievedMemory(**m) for m in retrieved_raw]
            prov = Provenance(
                user_query=provenance_seed.get("user_query", ""),
                recall_query=provenance_seed.get("recall_query", ""),
                expanded_queries=list(provenance_seed.get("expanded_queries", [])),
                retrieved=retrieved,
                cited=[n for n in _citations_in(full_reply) if 1 <= n <= len(retrieved)],
                phantoms=phantoms,
                conv_id=None,
                skipped_meta_query=bool(provenance_seed.get("skipped_meta_query", False)),
            )
            asyncio.create_task(_write_provenance_async(prov))
        except Exception as exc:
            logger.warning("Provenance sidecar skipped: %s", exc)


def _citations_in(text: str) -> list[int]:
    # Tiny helper so _openai_stream_response doesn't import extract_citations
    # at the top (keeps the symbol list there short). Provenance parsing is
    # cheap; no need to cache.
    from .provenance import extract_citations  # noqa: PLC0415
    return extract_citations(text)


async def _write_provenance_async(prov: Provenance) -> None:
    try:
        await asyncio.to_thread(write_provenance, prov)
    except Exception as exc:
        logger.warning("Provenance write failed: %s", exc)


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


@app.post("/api/models/unload")
async def unload_model() -> JSONResponse:
    """Stop the inference backend and free GPU/RAM."""
    assert _inference_backend is not None
    await _inference_backend.stop()
    return JSONResponse({"ok": True})


@app.get("/api/models/active")
async def active_model() -> JSONResponse:
    """Return info about the currently loaded model."""
    assert _inference_backend is not None
    return JSONResponse({
        "name": _inference_backend.current_model(),
        "backend": _inference_backend.current_backend(),
        "api_base": _inference_backend.api_base(),
        "running": _inference_backend.is_running(),
        # Adapter is null unless a LoRA adapter was activated on this model.
        # The UI uses this to render the "<model> + <adapter>" pill.
        "adapter": _model_manager.current_adapter_name() if _model_manager else None,
    })


@app.get("/api/adapters")
async def list_adapters(model: str) -> JSONResponse:
    """Return the LoRA adapters available under a given model's directory.

    The `model` parameter is the model's display name (same as returned
    by `/api/local-models`). Adapters live in `<model>/adapters/<name>/`
    so the filesystem layout enforces base-model compatibility — callers
    can only see adapters that were trained against this base.
    """
    assert _model_manager is not None
    return JSONResponse({"adapters": _model_manager.list_adapters(model)})


@app.post("/api/adapters/activate")
async def activate_adapter(request: Request) -> JSONResponse:
    """Activate (or clear) a LoRA adapter on the given base model.

    Body: `{"model": "<model-name>", "adapter": "<adapter-name>" | null}`.

    Restarts `mlx_lm.server` with `--adapter-path` pointing at the
    resolved adapter directory. ~2–3s on a warm file cache for a 7B Q4
    model — the UI shows a progress indicator while this runs.
    Passing `adapter: null` relaunches without an adapter (deactivate).
    """
    assert _model_manager is not None
    body = await request.json()
    model = (body.get("model") or "").strip()
    adapter = body.get("adapter")
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)
    try:
        name, api_base = await _model_manager.load(model, adapter=adapter)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "model": name, "adapter": adapter, "api_base": api_base,
    })


@app.get("/api/backend/mode")
async def get_backend_mode() -> JSONResponse:
    """Return the current backend mode (native vs LM Studio)."""
    assert _inference_backend is not None
    return JSONResponse({
        "lm_studio": _inference_backend.lm_studio_mode,
        "lm_studio_url": _inference_backend.lm_studio_url,
    })


@app.patch("/api/backend/mode")
async def set_backend_mode(request: Request) -> JSONResponse:
    """Switch between native inference backend and LM Studio at runtime."""
    assert _inference_backend is not None
    body = await request.json()
    lm_studio: bool = bool(body.get("lm_studio", False))
    lm_studio_url: str = str(body.get("lm_studio_url", "http://localhost:1234")).strip()
    if not lm_studio_url:
        lm_studio_url = "http://localhost:1234"

    if _inference_backend.lm_studio_mode and not lm_studio:
        # Switching back to native — nothing running yet, that's fine
        logger.info("Switching to native inference backend")
    elif not _inference_backend.lm_studio_mode and lm_studio:
        # Switching to LM Studio — stop the managed process
        await _inference_backend.stop()
        logger.info(f"Switching to LM Studio at {lm_studio_url}")

    _inference_backend.lm_studio_mode = lm_studio
    _inference_backend.lm_studio_url = lm_studio_url

    # Persist to config.yaml
    _config.setdefault("inference", {})["external_server"] = lm_studio
    _config["inference"]["external_server_url"] = lm_studio_url
    # Remove legacy keys if present
    _config["inference"].pop("lm_studio", None)
    _config["inference"].pop("lm_studio_url", None)
    config_path = os.environ.get(
        "ORCHESTRATOR_CONFIG",
        os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
    )
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(_config, f, default_flow_style=False, allow_unicode=True)

    return JSONResponse({
        "ok": True,
        "lm_studio": lm_studio,
        "lm_studio_url": lm_studio_url,
    })


@app.get("/api/config/models-dir")
async def api_get_models_dir() -> JSONResponse:
    """Return the directory where models are scanned from / downloaded to."""
    assert _config is not None
    raw = _config.get("inference", {}).get("models_dir", "~/loca_models")
    return JSONResponse({"models_dir": os.path.expanduser(str(raw))})


@app.put("/api/config/models-dir")
async def api_put_models_dir(request: Request) -> JSONResponse:
    """Change the models directory. Persists to config.yaml and updates
    the in-memory managers so a scan can find models in the new location
    without a restart. If the active-model path becomes invalid in the new
    directory, the UI should prompt to re-select one."""
    body = await request.json()
    raw = body.get("models_dir")
    if not isinstance(raw, str) or not raw.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "models_dir must be a non-empty string"}},
        )
    from pathlib import Path as _Path  # noqa: PLC0415
    new_dir = os.path.expanduser(raw.strip())

    assert _config is not None
    _config.setdefault("inference", {})["models_dir"] = new_dir
    config_path = os.environ.get(
        "ORCHESTRATOR_CONFIG",
        os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
    )
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(_config, f, default_flow_style=False, allow_unicode=True)

    # Update in-memory state — no restart needed for scanning / downloads.
    if _model_manager is not None:
        _model_manager.models_dir = _Path(new_dir)
        _model_manager.gguf_dir = _Path(new_dir) / "gguf"
        _model_manager.mlx_dir = _Path(new_dir) / "mlx"
    if _inference_backend is not None:
        _inference_backend.models_dir = _Path(new_dir)
    if _voice_backend is not None:
        _voice_backend.cfg.models_dir = _Path(new_dir)

    return JSONResponse({"ok": True, "models_dir": new_dir})


@app.get("/api/server-status")
async def server_status() -> JSONResponse:
    """Return model/server availability for the current inference mode."""
    assert _model_manager is not None
    assert _inference_backend is not None

    if _inference_backend.lm_studio_mode:
        url = _inference_backend.lm_studio_url.rstrip("/")
        # Detect server type from URL
        if "11434" in url:
            mode = "ollama"
        elif "1234" in url:
            mode = "lm_studio"
        else:
            mode = "custom"

        running = False
        models: list[str] = []
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{url}/models")
                if r.status_code == 200:
                    running = True
                    data = r.json()
                    models = [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            pass

        return JSONResponse({
            "mode": mode,
            "server_running": running,
            "models": models,
            "url": url,
        })
    else:
        loaded = _inference_backend.current_model()
        return JSONResponse({
            "mode": "native",
            "model_loaded": loaded is not None,
            "model_name": loaded,
        })


@app.post("/api/server/start")
async def start_external_server() -> JSONResponse:
    """Attempt to open the configured external inference server app."""
    assert _inference_backend is not None
    if not _inference_backend.lm_studio_mode:
        return JSONResponse({"error": "Not in external server mode"}, status_code=400)

    import subprocess as _subprocess
    url = _inference_backend.lm_studio_url
    app_name = "Ollama" if "11434" in url else "LM Studio"
    try:
        _subprocess.Popen(
            ["open", "-a", app_name],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
        )
        return JSONResponse({"ok": True, "app": app_name})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/models/load")
async def load_model(request: Request) -> JSONResponse:
    """Load a local model into the inference backend."""
    assert _model_manager is not None
    body = await request.json()
    name = body.get("name", "")
    ctx_size = body.get("ctx_size")
    n_gpu_layers = body.get("n_gpu_layers")
    batch_size = body.get("batch_size")
    num_threads = body.get("num_threads")
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    try:
        model_name, api_base = await _model_manager.load(
            name, ctx_size, n_gpu_layers, batch_size, num_threads
        )
        # Backfill embeddings for memories stored before a model was available
        if _orchestrator:
            asyncio.create_task(_orchestrator.backfill_embeddings())
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
    _download_meta[download_id] = {"repo_id": repo_id, "filename": filename, "format": fmt}

    # Prime the SSE stream with an immediate indeterminate event so the
    # client's progress bar appears within ~one render frame instead of
    # waiting 1–3 s for HF API resolution + first chunk. Without this
    # the user clicks Get and stares at nothing, then assumes it hung.
    from .model_manager import DownloadProgress as _DP  # noqa: PLC0415
    await queue.put(_DP(percent=-1))

    async def _run_download() -> None:
        try:
            async for progress in _model_manager.download(repo_id, filename, fmt):
                await queue.put(progress)
        except asyncio.CancelledError:
            await queue.put(_DP(0, error="cancelled"))
        finally:
            _download_tasks.pop(download_id, None)

    task = asyncio.create_task(_run_download())
    _download_tasks[download_id] = task
    return JSONResponse({"download_id": download_id})


@app.get("/api/models/download/{download_id}/progress", response_model=None)
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


@app.post("/api/models/download/{download_id}/cancel")
async def cancel_download(download_id: str) -> JSONResponse:
    """Cancel a running download and delete any partial files."""
    task = _download_tasks.pop(download_id, None)
    meta = _download_meta.pop(download_id, None)
    _download_jobs.pop(download_id, None)

    if task and not task.done():
        task.cancel()

    # Delete partial destination so a future download starts clean
    if meta and _model_manager:
        fmt = meta.get("format", "")
        if fmt == "mlx":
            dest = _model_manager.mlx_dir / meta["repo_id"].split("/")[-1]
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
        elif meta.get("filename"):
            dest = _model_manager.gguf_dir / meta["filename"]
            dest.unlink(missing_ok=True)

    return JSONResponse({"ok": True})


@app.post("/api/models/download/{download_id}/pause")
async def pause_download(download_id: str) -> JSONResponse:
    """Pause a running download. Keeps already-downloaded files so resume can continue."""
    task = _download_tasks.pop(download_id, None)
    _download_meta.pop(download_id, None)
    _download_jobs.pop(download_id, None)

    if task and not task.done():
        task.cancel()

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# /api/upload — process attached files before including them in a message
# ---------------------------------------------------------------------------

def _extract_via_adapter(adapter_cls, data: bytes, filename: str) -> str:
    """Write bytes to a temp file, run an importer adapter, and join the chunks.

    Mirrors the file → adapter → chunks flow used by the knowledge-import
    pipeline so chat attachments and imports see the same extraction quality.
    """
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    suffix = Path(filename).suffix or ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        chunks = adapter_cls().extract(tmp_path)
        if not chunks:
            return f"[Could not extract text from {filename}]"
        return "\n\n".join(c.text for c in chunks)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> JSONResponse:
    """
    Accept a file upload and return a content descriptor:
      - image/*           → {"type": "image", "data": "data:…;base64,…", "name": …}
      - .pdf              → {"type": "text",  "content": "<extracted text>", "name": …}
      - .xlsx / .csv      → {"type": "text",  "content": "<row-by-row>",    "name": …}
      - .docx             → {"type": "text",  "content": "<paragraphs>",    "name": …}
      - .epub             → {"type": "text",  "content": "<chapters>",      "name": …}
      - audio/*           → {"type": "audio", "name": …} or transcribed text
      - video/*           → {"type": "video", "name": …}
      - other utf-8 text  → {"type": "text",  "content": "<utf-8 text>",    "name": …}
      - otherwise         → {"type": "binary", "name": …}
    """
    content_type = (file.content_type or "").lower()
    filename = file.filename or "upload"
    lower_name = filename.lower()
    data = await file.read()

    if content_type.startswith("image/"):
        b64 = base64.b64encode(data).decode()
        return JSONResponse({
            "type": "image",
            "data": f"data:{content_type};base64,{b64}",
            "name": filename,
        })

    if content_type == "application/pdf" or lower_name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception as exc:
            text = f"[Could not extract PDF text: {exc}]"
        return JSONResponse({"type": "text", "content": text, "name": filename})

    if lower_name.endswith((".xlsx", ".csv")):
        from .importers.adapters.spreadsheet import SpreadsheetAdapter  # noqa: PLC0415
        text = _extract_via_adapter(SpreadsheetAdapter, data, filename)
        return JSONResponse({"type": "text", "content": text, "name": filename})

    if lower_name.endswith(".docx"):
        from .importers.adapters.docx import DocxAdapter  # noqa: PLC0415
        text = _extract_via_adapter(DocxAdapter, data, filename)
        return JSONResponse({"type": "text", "content": text, "name": filename})

    if lower_name.endswith(".epub"):
        from .importers.adapters.epub import EpubAdapter  # noqa: PLC0415
        text = _extract_via_adapter(EpubAdapter, data, filename)
        return JSONResponse({"type": "text", "content": text, "name": filename})

    if content_type.startswith("audio/"):
        # Attempt transcription if voice backend is available
        if _voice_backend:
            try:
                result = await _voice_backend.transcribe(audio_data=data)
                return JSONResponse({
                    "type": "text",
                    "content": result.get("text", ""),
                    "name": filename,
                    "source": "voice_transcription",
                })
            except Exception as exc:
                logger.warning(f"Audio transcription failed, returning raw: {exc}")
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
async def index() -> Response:
    """Primary UI — the Svelte bundle under src/static/ui/. A missing
    bundle means `npm run build --prefix ui` hasn't been run on this
    checkout; return a helpful error instead of a white page."""
    ui_index = os.path.join(_STATIC, "ui", "index.html")
    if os.path.isfile(ui_index):
        return FileResponse(
            ui_index,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return Response(
        status_code=503,
        content="Svelte UI not built. Run `npm run build --prefix ui` first.",
    )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.get("/api/llama/version")
async def llama_version() -> JSONResponse:
    """Return the installed llama-server build number and whether an upgrade is available."""
    bin_path = _inference_backend.llama_server_bin if _inference_backend else "llama-server"
    build = await InferenceBackend.get_llama_build(bin_path)
    outdated = await InferenceBackend.is_llama_outdated(bin_path)

    return JSONResponse(content={
        "build": build,
        "outdated": outdated,
        "upgrade_cmd": "brew upgrade llama.cpp",
    })


@app.get("/assets/{file_path:path}")
async def serve_asset(file_path: str) -> Response:
    """Serve static assets (JS, CSS) bundled with the app."""
    base = os.path.realpath(os.path.join(_STATIC, "assets"))
    full = os.path.realpath(os.path.join(base, file_path))
    if not full.startswith(base + os.sep) and full != base:
        return Response(status_code=404)
    if os.path.isfile(full):
        return FileResponse(full)
    return Response(status_code=404)


# /ui — Svelte UI served from src/static/ui/ (produced by `npm run build`
# in the `ui/` workspace). Directory may not exist on first-time checkouts
# that haven't run the build yet; in that case /ui returns 404 cleanly
# rather than crashing the server on startup.
_UI_ROOT = os.path.realpath(os.path.join(_STATIC, "ui"))


@app.get("/ui", include_in_schema=False)
@app.get("/ui/", include_in_schema=False)
async def ui_index() -> Response:
    index_path = os.path.join(_UI_ROOT, "index.html")
    if not os.path.isfile(index_path):
        return Response(
            status_code=404,
            content="Svelte UI not built. Run `npm run build --prefix ui` first.",
        )
    return FileResponse(
        index_path,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/ui/{file_path:path}", include_in_schema=False)
async def ui_asset(file_path: str) -> Response:
    """Serve Svelte-built assets under /ui/. Unknown paths fall back to
    index.html so the Svelte SPA can handle client-side routes like
    /ui/preferences or /ui/glossary."""
    full = os.path.realpath(os.path.join(_UI_ROOT, file_path))
    if not full.startswith(_UI_ROOT + os.sep) and full != _UI_ROOT:
        return Response(status_code=404)
    if os.path.isfile(full):
        return FileResponse(full)
    # SPA catch-all: serve index.html for any non-asset path under /ui.
    index_path = os.path.join(_UI_ROOT, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(
            index_path,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return Response(status_code=404)


@app.get("/system-stats")
async def system_stats() -> JSONResponse:
    """Report system-wide RAM use, matching Activity Monitor's
    'Memory Used' (App + Wired + Compressed). The previous
    implementation hard-coded page = 4096, which quartered the
    reading on Apple Silicon (16384-byte pages)."""
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

        # Parse the page size from `vm_stat`'s header — Apple Silicon
        # reports 16384, Intel reports 4096. SC_PAGE_SIZE is a reliable
        # fallback if the header format ever shifts.
        page_match = re.search(r"page size of (\d+) bytes", text)
        page = int(page_match.group(1)) if page_match else os.sysconf("SC_PAGE_SIZE")

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
    # `adapter` (nullable) pins this conversation to a specific LoRA
    # adapter — overrides the project's binding so siblings can use
    # different adapters in the same session. Sending `null` clears
    # the override; omit the key to leave it alone.
    if "adapter" in body:
        kwargs["adapter_name"] = body.get("adapter")
    patch_conversation(conv_id, **kwargs)
    return JSONResponse({"ok": True})


@app.delete("/api/conversations/{conv_id}")
async def api_delete_conversation(conv_id: str) -> JSONResponse:
    delete_conversation(conv_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Research Projects API — the Research Partner feature
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def api_list_projects() -> JSONResponse:
    return JSONResponse({"projects": list_projects()})


@app.post("/api/projects")
async def api_create_project(request: Request) -> JSONResponse:
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    scope = (body.get("scope") or "").strip()
    pid = create_project(title=title, scope=scope)
    return JSONResponse({"id": pid, "project": get_project(pid)})


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str) -> JSONResponse:
    proj = get_project(project_id)
    if not proj:
        return JSONResponse({"error": "not found"}, status_code=404)
    proj["items_count"] = count_project_items(project_id)
    proj["conversations"] = list_project_conversations(project_id)
    proj["watches"] = list_project_watches(project_id)
    return JSONResponse(proj)


@app.patch("/api/projects/{project_id}")
async def api_patch_project(project_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    kwargs: dict = {}
    if "title" in body:
        kwargs["title"] = body["title"]
    if "scope" in body:
        kwargs["scope"] = body["scope"]
    if "notes" in body:
        kwargs["notes"] = body["notes"]
    # `adapter` (nullable) binds a preferred LoRA adapter to the project.
    # The client explicitly sending `null` clears the binding; omit the
    # key to leave it alone.
    if "adapter" in body:
        kwargs["adapter_name"] = body["adapter"]
    # `obsidian_source` opts the project into live retrieval over every
    # watched Obsidian vault — no per-project ingestion, the orchestrator
    # queries `vault_notes` directly each turn.
    if "obsidian_source" in body:
        kwargs["obsidian_source"] = bool(body["obsidian_source"])
    patch_project(project_id, **kwargs)
    return JSONResponse({"ok": True, "project": get_project(project_id)})


@app.post("/api/conversations/{conv_id}/activate-adapter")
async def api_conv_activate_adapter(conv_id: str) -> JSONResponse:
    """Activate the conversation's adapter (or its project's adapter,
    or base) on the currently loaded model. Layered fallback so the
    per-conv override always wins, with the project's binding as a
    sensible default when no override is set."""
    assert _model_manager is not None
    conv = get_conversation(conv_id)
    if not conv:
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    adapter = conv.get("adapter_name")
    if adapter is None:
        # Fall back to the project's binding if any.
        pid = conv.get("project_id")
        if pid:
            proj = get_project(pid)
            if proj:
                adapter = proj.get("adapter_name")
    active_model = _inference_backend.current_model() if _inference_backend else None
    if not active_model:
        return JSONResponse({"ok": True, "model": None, "adapter": None})
    try:
        name, api_base = await _model_manager.load(active_model, adapter=adapter)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "model": name, "adapter": adapter, "api_base": api_base,
    })


@app.post("/api/projects/{project_id}/activate-adapter")
async def api_project_activate_adapter(project_id: str) -> JSONResponse:
    """Activate the project's stored adapter on the currently loaded model.

    Called by the UI when the user switches to a project — keeps the
    adapter-routing logic server-side so both clients share the same
    policy (incompatible adapter → clear error, no adapter bound →
    deactivate, missing model → no-op).
    """
    assert _model_manager is not None
    proj = get_project(project_id)
    if not proj:
        return JSONResponse({"error": "project not found"}, status_code=404)
    adapter = proj.get("adapter_name")
    active_model = _inference_backend.current_model() if _inference_backend else None
    if not active_model:
        # No model loaded — the UI shouldn't even show the picker in
        # this state, but handle gracefully just in case.
        return JSONResponse({"ok": True, "model": None, "adapter": None})
    try:
        name, api_base = await _model_manager.load(active_model, adapter=adapter)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "model": name, "adapter": adapter, "api_base": api_base,
    })


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str) -> JSONResponse:
    delete_project(project_id)
    return JSONResponse({"ok": True})


@app.get("/api/projects/{project_id}/items")
async def api_list_project_items(
    project_id: str, kind: str | None = None, limit: int = 200, offset: int = 0,
) -> JSONResponse:
    items = list_project_items(project_id, kind=kind, limit=limit, offset=offset)
    return JSONResponse({"items": items, "total": count_project_items(project_id)})


@app.post("/api/projects/{project_id}/items")
async def api_add_project_item(project_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    kind = body.get("kind")
    if not kind:
        return JSONResponse({"error": "kind is required"}, status_code=400)
    import hashlib
    title = (body.get("title") or "").strip()
    article_body = (body.get("body") or "").strip()
    ref_id = body.get("ref_id")
    url = body.get("url")
    raw = (url or "") + "\n" + title + "\n" + article_body
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw.strip() else ""
    try:
        iid = add_project_item(
            project_id,
            kind=kind, title=title, body=article_body,
            ref_id=ref_id, url=url, content_hash=content_hash,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if iid is None:
        return JSONResponse({"ok": True, "duplicate": True})
    return JSONResponse({"id": iid})


@app.delete("/api/projects/{project_id}/items/{item_id}")
async def api_delete_project_item(project_id: str, item_id: str) -> JSONResponse:
    delete_project_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/api/projects/{project_id}/attach-conversation")
async def api_attach_conversation(project_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    conv_id = body.get("conv_id")
    if not conv_id:
        return JSONResponse({"error": "conv_id is required"}, status_code=400)
    # Set the FK so the orchestrator treats this conv as scoped to the
    # project during retrieval…
    set_conversation_project(conv_id, project_id)
    # …and mirror it into project_items so the Sources list shows the
    # attachment. Without this row, Sources (which reads project_items)
    # silently hides attached conversations even though they're scoped —
    # users reported "Attach current conversation didn't do anything".
    import hashlib as _hashlib  # noqa: PLC0415
    conv = get_conversation(conv_id)
    title = (conv or {}).get("title") or conv_id
    content_hash = _hashlib.sha256(f"conv:{conv_id}".encode()).hexdigest()
    add_project_item(
        project_id,
        kind="conv",
        title=str(title),
        body="",
        ref_id=conv_id,
        content_hash=content_hash,
    )
    return JSONResponse({"ok": True})


@app.post("/api/projects/{project_id}/detach-conversation")
async def api_detach_conversation(project_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    conv_id = body.get("conv_id")
    if not conv_id:
        return JSONResponse({"error": "conv_id is required"}, status_code=400)
    set_conversation_project(conv_id, None)
    return JSONResponse({"ok": True})


@app.get("/api/projects/{project_id}/watches")
async def api_list_watches(project_id: str) -> JSONResponse:
    return JSONResponse({"watches": list_project_watches(project_id)})


@app.post("/api/projects/{project_id}/watches")
async def api_create_watch(project_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    sub_scope = (body.get("sub_scope") or "").strip()
    if not sub_scope:
        return JSONResponse({"error": "sub_scope is required"}, status_code=400)
    minutes = int(body.get("schedule_minutes") or 1440)
    wid = create_project_watch(project_id, sub_scope, minutes)
    return JSONResponse({"id": wid})


@app.delete("/api/projects/{project_id}/watches/{watch_id}")
async def api_delete_watch(project_id: str, watch_id: str) -> JSONResponse:
    delete_project_watch(watch_id)
    return JSONResponse({"ok": True})


@app.post("/api/projects/{project_id}/watches/{watch_id}/run")
async def api_run_watch(project_id: str, watch_id: str) -> JSONResponse:
    """Manually trigger a watch run, bypassing its schedule. Used by the
    "Run now" button in the Watches tab. Delegates to the same executor
    the background tick uses, so behaviour is identical — one tick's
    worth of search + diff + append + hash update."""
    from .watches_runner import run_watch_once  # noqa: PLC0415
    matches = [w for w in list_project_watches(project_id) if w["id"] == watch_id]
    if not matches:
        return JSONResponse({"error": "watch not found"}, status_code=404)
    watch = matches[0]
    try:
        result = await run_watch_once(
            watch,
            searxng_url=os.environ.get("SEARXNG_URL", "http://localhost:8888"),
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": "watch timed out while searching"}, status_code=504,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "result": result.to_dict()})


@app.get("/api/projects/{project_id}/related")
async def api_project_related(project_id: str, limit: int = 10) -> JSONResponse:
    """Link discovery — semantic neighbours to the project's scope text.
    Unions TF-IDF matches from every indexed vault with recall hits from
    the memory store. Surfaces "you've written about this before" notes
    that aren't explicitly bookmarked yet."""
    proj = get_project(project_id)
    if not proj:
        return JSONResponse({"error": "project not found"}, status_code=404)
    scope = (proj.get("scope") or "").strip()
    if not scope:
        return JSONResponse({"items": [], "message": "Project has no scope text yet."})

    # Thresholds. TF-IDF cosine over title+tags+snippet is sparse, so 0.15
    # keeps real topical matches and kills stopword noise. Memory recall
    # is dense-embedding cosine, where typical unrelated hits land around
    # 0.25–0.35; 0.35 is the tightest cutoff that still shows tangential
    # memories without dragging in "Learning about Cars" on a neuroscience
    # project.
    VAULT_MIN = 0.15
    MEMORY_MIN = 0.35

    from .vault_search import semantic_search  # noqa: PLC0415
    results: list[dict] = []
    per_source = max(3, limit)
    for vpath in list_vault_paths():
        for hit in semantic_search(vpath, scope, limit=per_source, min_score=VAULT_MIN):
            results.append({
                "kind": "vault_chunk",
                "title": hit.get("title") or hit.get("rel_path"),
                "snippet": hit.get("snippet", ""),
                "score": hit.get("score", 0.0),
                "vault_path": vpath,
                "rel_path": hit.get("rel_path"),
            })
    if _plugin_manager is not None and _plugin_manager.memory_plugin is not None:
        try:
            hits = await _plugin_manager.memory_plugin.recall(scope, limit=per_source)
        except Exception:
            hits = []
        for h in hits:
            snippet = (h.get("content") or "")[:240]
            raw_score = h.get("score")
            if raw_score is None:
                dist = h.get("distance")
                raw_score = -float(dist) if dist is not None else 0.0
            if float(raw_score) < MEMORY_MIN:
                continue
            results.append({
                "kind": "memory",
                "title": snippet[:80],
                "snippet": snippet,
                "score": float(raw_score),
                "memory_id": h.get("id"),
            })
    # Polish pass (omnibus #92):
    #   1. Strip Obsidian wiki-link syntax — `[[target|display]]` →
    #      `display`, `[[target]]` → `target`. Raw brackets looked
    #      like markup in the UI.
    #   2. Drop rows whose snippet is just YAML frontmatter (leading
    #      `---\ntags: ...\n---`) — those are metadata lines that
    #      happened to score above the threshold, not actual content.
    #   3. Truncate long titles at a word boundary with `…`, not mid-
    #      word, so "What notati…" becomes "What…".
    #   4. Dedup across memory+vault by *normalised* title, preferring
    #      the higher-scoring origin. Before this change a note that
    #      existed as both an ingested memory chunk and a vault chunk
    #      appeared twice on the Related Notes card.
    for r in results:
        r["title"] = _truncate_words(_strip_wikilinks(r.get("title") or ""), 72)
        r["snippet"] = _strip_wikilinks(r.get("snippet", ""))
    results = [r for r in results if not _is_frontmatter_only(r.get("snippet", ""))]
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    dedup: dict[str, dict] = {}
    for r in results:
        key = _norm_title(r.get("title") or "")
        if not key:
            continue
        existing = dedup.get(key)
        if existing is None or r.get("score", 0.0) > existing.get("score", 0.0):
            dedup[key] = r
    ranked = sorted(dedup.values(), key=lambda r: r.get("score", 0.0), reverse=True)
    return JSONResponse({"items": ranked[:limit]})


# ---- Related-Notes polish helpers ----

def _strip_wikilinks(text: str) -> str:
    """Collapse Obsidian `[[target|display]]` to `display` and
    `[[target]]` to `target`. Keeps prose readable when a vault note
    has raw wiki-link syntax in its headers."""
    import re  # noqa: PLC0415
    def repl(m: "re.Match[str]") -> str:
        body = m.group(1)
        return body.split("|", 1)[1] if "|" in body else body
    return re.sub(r"\[\[([^\[\]]+)\]\]", repl, text)


def _is_frontmatter_only(snippet: str) -> bool:
    """True if the snippet is nothing but a YAML frontmatter block.
    These slip through because tags happen to match the scope query
    but aren't useful content in Related Notes."""
    s = snippet.strip()
    if not s.startswith("---"):
        return False
    # After the first `---`, everything up to the closing `---` (if any)
    # is frontmatter. If the snippet has no actual body after it, drop.
    closing = s.find("---", 3)
    if closing == -1:
        return True
    body = s[closing + 3:].strip()
    return not body


def _norm_title(title: str) -> str:
    """Lowercase + collapse whitespace so 'Al-Andalus' and 'al-andalus '
    dedup together. Avoid unicode normalisation — titles are already
    user-facing display strings."""
    return " ".join(title.lower().split())


def _truncate_words(text: str, max_chars: int) -> str:
    """Cut at the last word boundary within budget and append `…`."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Find the last whitespace so we don't slice mid-word.
    space = cut.rfind(" ")
    if space > max_chars // 2:  # don't leave an empty truncation
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


@app.post("/api/projects/{project_id}/dig-deeper")
async def api_dig_deeper(project_id: str, request: Request) -> JSONResponse:
    """Dig deeper — bounded web-research run on a sub-scope. Fetches the
    top `max_results` search hits, imports them through the knowledge
    pipeline (so they land in memory globally), and records URL
    bookmarks on the project so the user can revisit the trail."""
    if _plugin_manager is None or _plugin_manager.memory_plugin is None:
        return JSONResponse({"error": "Memory plugin unavailable"}, status_code=503)
    proj = get_project(project_id)
    if not proj:
        return JSONResponse({"error": "project not found"}, status_code=404)
    body = await request.json()
    sub_scope = (body.get("sub_scope") or "").strip()
    max_results = int(body.get("max_results") or 5)
    if not sub_scope:
        return JSONResponse({"error": "sub_scope is required"}, status_code=400)

    from .importers.service import build_default_service  # noqa: PLC0415
    from .tools.web_search import web_search  # noqa: PLC0415

    hits = await web_search(
        query=sub_scope,
        searxng_url=os.environ.get("SEARXNG_URL", "http://localhost:8888"),
        max_results=max_results,
        research_mode=False,
    )

    svc = build_default_service(_plugin_manager.memory_plugin)  # type: ignore[arg-type]
    bookmarks: list[dict] = []
    import hashlib as _hashlib  # noqa: PLC0415
    for hit in hits:
        url = hit.url
        title = (hit.title or url).strip()
        if not url:
            continue
        # Import into memory for global recall, best-effort.
        try:
            async for _evt in svc.run(url):
                pass
        except Exception:
            pass
        # Bookmark the URL on the project regardless of import outcome.
        content_hash = _hashlib.sha256(f"url:{url}".encode()).hexdigest()
        iid = add_project_item(
            project_id,
            kind="web_url",
            title=title,
            body=(hit.snippet or "")[:500],
            url=url,
            content_hash=content_hash,
        )
        bookmarks.append({
            "id": iid, "url": url, "title": title,
            "duplicate": iid is None,
        })
    return JSONResponse({
        "sub_scope": sub_scope,
        "bookmarks": bookmarks,
        "total": len(bookmarks),
    })


_SYNC_LOCKS: dict[str, asyncio.Lock] = {}
_SYNC_BUSY: set[str] = set()


def _sync_lock_for(project_id: str) -> asyncio.Lock:
    """One lock per project. Prevents concurrent syncs on the same
    project from racing on `vault_chunk` dedup; different projects can
    still sync in parallel."""
    lock = _SYNC_LOCKS.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _SYNC_LOCKS[project_id] = lock
    return lock


@app.get("/api/projects/sync-busy")
async def api_sync_busy() -> JSONResponse:
    """Snapshot of which project IDs currently have a vault sync in
    flight. UI polls this cheaply to grey out conflicting actions
    (switching projects, starting another sync, etc.)."""
    return JSONResponse({"busy": sorted(_SYNC_BUSY)})


@app.post("/api/projects/{project_id}/sync-vault")
async def api_sync_vault(project_id: str, request: Request) -> JSONResponse:
    """Absorbs the old Knowledge-Memory Link workflow as a project-scoped
    action: ingest a vault (or any importable path) into the shared
    memory store via the existing knowledge-import pipeline, then record
    a `vault_sync` project_item that tracks path, last-sync time, and
    counts. Re-running on an unchanged vault stores 0 new chunks thanks
    to content-hash dedup inside ImportService.

    Concurrency fix (#92): the rglob + per-file read + sklearn TF-IDF
    ranking used to run directly on the event loop, so any other API
    call (setActiveProject, /api/models/load, etc.) blocked until sync
    finished — freezing the UI. All blocking work now runs in a thread
    via `asyncio.to_thread`. A per-project lock also stops a second
    click from kicking off a concurrent run on the same project.
    """
    if _plugin_manager is None or _plugin_manager.memory_plugin is None:
        return JSONResponse({"error": "Memory plugin unavailable"}, status_code=503)
    body = await request.json()
    path_str = (body.get("path") or "").strip()
    if not path_str:
        return JSONResponse({"error": "path is required"}, status_code=400)
    proj = get_project(project_id)
    if not proj:
        return JSONResponse({"error": "project not found"}, status_code=404)

    from .importers.service import build_default_service  # noqa: PLC0415
    svc = build_default_service(_plugin_manager.memory_plugin)  # type: ignore[arg-type]

    lock = _sync_lock_for(project_id)
    if lock.locked():
        return JSONResponse(
            {"error": "sync already in progress for this project"},
            status_code=409,
        )
    async with lock:
        _SYNC_BUSY.add(project_id)
        try:
            result = await _run_vault_sync(project_id, path_str, proj, svc)
        finally:
            _SYNC_BUSY.discard(project_id)
    return result


async def _run_vault_sync(
    project_id: str, path_str: str, proj: dict, svc: "object",
) -> JSONResponse:
    stored = 0
    skipped = 0
    total = 0
    errors: list[str] = []
    # ImportService.run is an async generator but the generator body does
    # blocking adapter.extract + SQLite upsert work between yields. Drive
    # it from a thread so it can't stall the event loop.
    async for event in svc.run(path_str):  # type: ignore[attr-defined]
        if event.get("status") == "error":
            errors.append(str(event.get("message", "")))
        if event.get("status") == "done":
            stored = int(event.get("stored", 0))
            skipped = int(event.get("skipped", 0))
            total = int(event.get("total", 0))

    import hashlib as _hashlib  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    stats = {
        "path": path_str,
        "stored": stored,
        "skipped": skipped,
        "total": total,
        "errors": errors,
        "synced_at": _utcnow_ts(),
    }
    # Hash the path alone so re-syncing the same vault upserts rather
    # than piling up `vault_sync` items.
    content_hash = _hashlib.sha256(f"vault:{path_str}".encode()).hexdigest()
    for existing in list_project_items(project_id, kind="vault_sync", limit=200):
        if existing.get("content_hash") == content_hash:
            delete_project_item(existing["id"])
    add_project_item(
        project_id,
        kind="vault_sync",
        title=f"Vault: {path_str}",
        body=_json.dumps(stats),
        url=path_str,
        content_hash=content_hash,
    )
    # The per-note pass — rglob, TF-IDF rank, per-file read, per-note
    # add_project_item — is CPU + disk heavy. Run it in a thread so it
    # doesn't block the event loop.
    scope_text = str(proj.get("scope") or "").strip()
    note_count = await asyncio.to_thread(
        _sync_vault_per_note, project_id, path_str, scope_text,
    )
    stats["notes_bookmarked"] = note_count
    stats["scope_filtered"] = bool(scope_text)
    return JSONResponse({"ok": True, **stats})


def _sync_vault_per_note(
    project_id: str, path_str: str, scope_text: str,
) -> int:
    """Blocking helper. Caller must wrap in `asyncio.to_thread`."""
    import hashlib as _hashlib  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415
    SCOPE_FILE_MIN = 0.05
    note_count = 0
    try:
        root = _Path(path_str).expanduser().resolve()
        if not root.is_dir():
            return 0
        md_files: list[_Path] = []
        for ext in ("*.md", "*.markdown"):
            md_files.extend(root.rglob(ext))
        md_files = list(dict.fromkeys(md_files))
        md_files = _rank_vault_files_by_scope(
            md_files, scope_text, min_score=SCOPE_FILE_MIN, limit=200,
        )
        for p in md_files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not text.strip():
                continue
            note_hash = _hashlib.sha256(
                (f"note:{p}:" + text).encode("utf-8", errors="replace")
            ).hexdigest()
            title = p.stem
            snippet = text.strip().replace("\n\n", "\n")[:500]
            iid = add_project_item(
                project_id,
                kind="vault_chunk",
                title=title,
                body=snippet,
                url=str(p),
                content_hash=note_hash,
            )
            if iid is not None:
                note_count += 1
    except Exception as exc:  # pragma: no cover
        logger.warning("vault sync item-pass failed: %s", exc)
    return note_count


def _rank_vault_files_by_scope(
    paths: list,  # list[pathlib.Path]
    scope: str,
    *,
    min_score: float,
    limit: int,
) -> list:
    """Rank markdown files by TF-IDF cosine similarity to `scope`.
    No scope → alphabetical cap. Any failure → alphabetical cap. Keeps
    the sync loop decoupled from sklearn details."""
    if not paths:
        return []
    if not scope.strip():
        return sorted(paths)[:limit]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415
        from sklearn.metrics.pairwise import cosine_similarity  # noqa: PLC0415
        docs: list[str] = []
        kept: list = []
        for p in paths:
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")[:4000]
            except Exception:
                continue
            docs.append(f"{p.stem}\n{raw}")
            kept.append(p)
        if not docs:
            return []
        vec = TfidfVectorizer(
            strip_accents="unicode", analyzer="word",
            min_df=1, sublinear_tf=True,
        )
        mat = vec.fit_transform(docs + [scope])
        sims = cosine_similarity(mat[-1], mat[:-1]).flatten()
        ranked = sorted(
            ((float(sims[i]), kept[i]) for i in range(len(kept))),
            key=lambda x: x[0], reverse=True,
        )
        filtered = [p for s, p in ranked if s > min_score][:limit]
        return filtered
    except Exception as exc:
        logger.warning("scope-aware vault ranking failed (%s); using alphabetical fallback", exc)
        return sorted(paths)[:limit]


def _utcnow_ts() -> str:
    import datetime as _dt  # noqa: PLC0415
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Memories API
# ---------------------------------------------------------------------------

@app.get("/api/memories")
async def api_list_memories(
    type: str | None = None, limit: int = 50, offset: int = 0
) -> JSONResponse:
    assert _plugin_manager is not None
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    page = _plugin_manager.memory_plugin.list_paged(type=type, limit=limit, offset=offset)
    return JSONResponse({
        "memories": page["items"],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    })


@app.post("/api/memories")
async def api_add_memory(request: Request) -> JSONResponse:
    assert _plugin_manager is not None
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    mid = await _plugin_manager.memory_plugin.store(content, {})
    return JSONResponse({"id": mid})


@app.patch("/api/memories/{mem_id}")
async def api_update_memory_plugin(mem_id: str, request: Request) -> JSONResponse:
    """Update memory content and clear its embedding so it's re-embedded on next recall."""
    assert _plugin_manager is not None
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    _plugin_manager.memory_plugin.update(mem_id, content)
    return JSONResponse({"ok": True})


@app.delete("/api/memories/{mem_id}")
async def api_delete_memory(mem_id: str) -> JSONResponse:
    assert _plugin_manager is not None
    _plugin_manager.memory_plugin.delete(mem_id)
    return JSONResponse({"ok": True})


@app.get("/api/memories/recall")
async def api_recall_memories(q: str, limit: int = 20) -> JSONResponse:
    """Semantic (or keyword) search over stored memories."""
    assert _plugin_manager is not None
    if not q.strip():
        return JSONResponse({"memories": []})
    results = await _plugin_manager.memory_plugin.recall(q.strip(), limit=limit)
    return JSONResponse({"memories": results})


# ---------------------------------------------------------------------------
# Knowledge import
# ---------------------------------------------------------------------------

@app.post("/api/import")
async def api_import(request: Request):  # noqa: ANN201
    """Stream import progress as SSE events."""
    if _plugin_manager is None or _plugin_manager.memory_plugin is None:
        return JSONResponse({"error": "Memory plugin not configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=422)

    path_str = body.get("path") if isinstance(body, dict) else None
    if not path_str:
        return JSONResponse({"error": "path is required"}, status_code=422)

    from .importers.service import build_default_service  # noqa: PLC0415

    svc = build_default_service(_plugin_manager.memory_plugin)  # type: ignore[arg-type]

    async def _stream():
        import json as _json  # noqa: PLC0415

        async for event in svc.run(path_str):
            yield f"data: {_json.dumps(event)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/api/import/history")
async def api_import_history() -> JSONResponse:
    """Return the list of past import runs."""
    return JSONResponse({"imports": list_import_history()})


# ---------------------------------------------------------------------------
# Plugin status
# ---------------------------------------------------------------------------

@app.get("/api/plugins")
async def api_plugins() -> JSONResponse:
    assert _plugin_manager is not None
    return JSONResponse(_plugin_manager.status())


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


@app.get("/api/suggest-params")
async def api_suggest_params(nvidia_vram_gb: float | None = None) -> JSONResponse:
    """Return suggested backend performance parameters for the current hardware.

    For Nvidia GPUs, pass ?nvidia_vram_gb=<float> to get n_gpu_layers suggestion.
    Returns {"source": "nvidia_no_vram"} when Nvidia is detected but VRAM is unknown.
    """
    from .hardware_profiler import get_hardware_profile, suggest_inference_params
    loop = asyncio.get_event_loop()
    profile = await loop.run_in_executor(None, get_hardware_profile)
    return JSONResponse(suggest_inference_params(profile, nvidia_vram_gb))


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


async def _build_recs_cache(force: bool = False) -> None:
    """Build the recommendations cache. Uses a lock so concurrent callers wait on the same build
    instead of spawning duplicate llmfit processes.

    get_hardware_profile / get_recommendations call blocking subprocesses, so they run in a
    thread executor to keep the asyncio event loop free (uvicorn stays responsive during startup).
    """
    global _recs_cache, _recs_cache_lock
    if _recs_cache_lock is None:
        _recs_cache_lock = asyncio.Lock()

    async with _recs_cache_lock:
        # If another task already built the cache while we were waiting, skip.
        if not force and _recs_cache is not None:
            return
        try:
            from .hardware_profiler import _llmfit_bin, get_hardware_profile, get_recommendations
            loop = asyncio.get_event_loop()
            profile = await loop.run_in_executor(None, get_hardware_profile)
            recs    = await loop.run_in_executor(None, get_recommendations, profile)
            # Skip HF API size-fetching here — those requests burn rate-limit budget
            # that downloads need. llmfit's own estimates are used instead.
            _recs_cache = {
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
                        "fit_level": r.fit_level,
                        "use_case": r.use_case,
                        "provider": r.provider,
                        "score": r.score,
                        "tps": r.tps,
                    }
                    for r in recs
                ],
            }
            logger.info(f"Recommendations cache built: {len(recs)} models")
        except Exception as e:
            logger.warning(f"Failed to build recommendations cache: {e}")


@app.get("/api/recommended-models")
async def api_recommended_models(force: bool = False) -> JSONResponse:
    """Return model recommendations. Cached after first build; ?force=true rebuilds.

    If the background warm-up is still running, this waits on the same lock instead of
    spawning a second llmfit process.
    """
    await _build_recs_cache(force=force)
    if _recs_cache is not None:
        return JSONResponse(_recs_cache)
    return JSONResponse({"total_ram_gb": 0, "has_apple_silicon": False, "llmfit_available": False, "recommendations": []})


async def _fetch_hf_actual_sizes(recs: list) -> dict[str, float]:
    """Fetch actual download sizes from HF API for each recommendation in parallel.

    For MLX repos: sums all sibling file sizes (same files the downloader will fetch).
    For GGUF: HEAD request on the specific file for Content-Length.
    Returns a dict of repo_id → size_gb; missing entries mean the estimate is used.
    """
    import httpx

    sem = asyncio.Semaphore(10)  # max 10 concurrent HF requests to avoid rate limiting

    async def _get_size(client: httpx.AsyncClient, rec) -> tuple[str, float | None]:
        async with sem:
            try:
                if rec.format == "mlx":
                    r = await client.get(
                        f"https://huggingface.co/api/models/{rec.repo_id}",
                        timeout=8,
                    )
                    if r.status_code == 200:
                        siblings = [
                            s for s in r.json().get("siblings", [])
                            if not s["rfilename"].endswith(".gitattributes")
                        ]
                        total = sum(s.get("size", 0) for s in siblings)
                        if total > 0:
                            return rec.repo_id, total / 1_073_741_824
                elif rec.filename:
                    r = await client.head(
                        f"https://huggingface.co/{rec.repo_id}/resolve/main/{rec.filename}",
                        follow_redirects=True,
                        timeout=8,
                    )
                    size = int(r.headers.get("content-length", 0))
                    if size > 0:
                        return rec.repo_id, size / 1_073_741_824
            except Exception:
                pass
            return rec.repo_id, None

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_get_size(client, r) for r in recs])
    return {repo_id: size for repo_id, size in results if size is not None}


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


@app.get("/api/repo-files")
async def api_repo_files(repo_id: str = "", format: str = "gguf") -> JSONResponse:
    """Return downloadable files for a HF repo, sorted with recommended quants first."""
    if not repo_id.strip():
        return JSONResponse({"files": []})
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://huggingface.co/api/models/{repo_id}")
            r.raise_for_status()
            siblings = r.json().get("siblings", [])
        ext = ".gguf" if format == "gguf" else ".safetensors"
        files = [
            {"name": s["rfilename"],
             "size_gb": round(s.get("size", 0) / 1_073_741_824, 2)}
            for s in siblings
            if s["rfilename"].lower().endswith(ext)
            and not s["rfilename"].startswith(".")
        ]
        if format == "gguf":
            quant_order = ["Q4_K_M", "Q5_K_M", "Q4_K_S", "Q6_K", "Q8_0", "IQ4_XS", "Q3_K_M", "Q2_K", "F16", "BF16"]
            def _quant_rank(fname: str) -> int:
                upper = fname.upper()
                for i, q in enumerate(quant_order):
                    if q in upper:
                        return i
                return 99
            files.sort(key=lambda f: _quant_rank(f["name"]))
        return JSONResponse({"files": files})
    except Exception as e:
        return JSONResponse({"files": [], "error": str(e)})


# ---------------------------------------------------------------------------
# Obsidian Watcher API — app-level background vault sync. See
# `src/obsidian_watcher.py` for the loop + registry; these endpoints
# are thin wrappers so the UI can register vaults, trigger an
# immediate scan, and poll status.
# ---------------------------------------------------------------------------

@app.get("/api/obsidian/watched")
async def api_obsidian_watched() -> JSONResponse:
    from . import obsidian_watcher  # noqa: PLC0415
    return JSONResponse({"vaults": obsidian_watcher.list_watched()})


@app.post("/api/obsidian/register")
async def api_obsidian_register(request: Request) -> JSONResponse:
    from . import obsidian_watcher  # noqa: PLC0415
    body = await request.json()
    path = body.get("path", "")
    scan_interval_s = int(body.get("scan_interval_s") or 300)
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    try:
        row = obsidian_watcher.register(path, scan_interval_s=scan_interval_s)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    # Kick off an immediate scan so the registered vault appears in
    # search results without making the user wait for the first tick.
    asyncio.create_task(_obsidian_first_scan(row["path"]))
    return JSONResponse({"ok": True, "vault": row})


async def _obsidian_first_scan(path: str) -> None:
    from . import obsidian_watcher  # noqa: PLC0415
    try:
        await obsidian_watcher.scan_now(path)
    except Exception as exc:
        logger.warning("first-scan for %s failed: %s", path, exc)


@app.post("/api/obsidian/unregister")
async def api_obsidian_unregister(request: Request) -> JSONResponse:
    from . import obsidian_watcher  # noqa: PLC0415
    body = await request.json()
    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    obsidian_watcher.unregister(path)
    return JSONResponse({"ok": True})


@app.post("/api/obsidian/scan-now")
async def api_obsidian_scan_now(request: Request) -> JSONResponse:
    from . import obsidian_watcher  # noqa: PLC0415
    body = await request.json()
    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    if obsidian_watcher.is_busy(path):
        return JSONResponse({"error": "scan already in progress"}, status_code=409)
    try:
        stats = await obsidian_watcher.scan_now(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "scan timed out"}, status_code=504)
    return JSONResponse({"ok": True, **stats})


@app.get("/api/obsidian/status")
async def api_obsidian_status() -> JSONResponse:
    from . import obsidian_watcher  # noqa: PLC0415
    return JSONResponse({
        "vaults": obsidian_watcher.list_watched(),
        "busy": obsidian_watcher.busy_paths(),
    })


# ---------------------------------------------------------------------------
# Vault API
# ---------------------------------------------------------------------------

@app.get("/api/vault/detect")
async def api_vault_detect() -> JSONResponse:
    from .vault_indexer import detect_vaults
    return JSONResponse({"vaults": detect_vaults()})


@app.post("/api/vault/scan")
async def api_vault_scan(request: Request) -> JSONResponse:
    from .vault_indexer import scan_vault, validate_vault_path
    body = await request.json()
    vault_path = body.get("path", "")
    if not vault_path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    err = validate_vault_path(vault_path)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    loop = asyncio.get_event_loop()
    try:
        stats = await loop.run_in_executor(None, scan_vault, vault_path)
        from .vault_search import clear_vault_search_cache
        clear_vault_search_cache(vault_path)
        return JSONResponse({"ok": True, **stats})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/vault/stats")
async def api_vault_stats(path: str = "") -> JSONResponse:
    from .vault_analyser import vault_stats
    if not path:
        return JSONResponse({"error": "path query param is required"}, status_code=400)
    return JSONResponse(vault_stats(path))


@app.get("/api/vault/analysis")
async def api_vault_analysis(path: str = "") -> JSONResponse:
    from .vault_analyser import full_analysis
    if not path:
        return JSONResponse({"error": "path query param is required"}, status_code=400)
    return JSONResponse(full_analysis(path))


@app.get("/api/vault/semantic-search")
async def api_vault_semantic_search(path: str = "", q: str = "", limit: int = 20) -> JSONResponse:
    if not path or not q.strip():
        return JSONResponse({"results": []})
    from .vault_search import semantic_search
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, semantic_search, path, q, limit)
        return JSONResponse({"results": results})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/vault/daily-notes")
async def api_vault_daily_notes(path: str = "") -> JSONResponse:
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    notes = list_vault_notes(path)
    daily = sorted(
        [n for n in notes if n.get("is_daily_note")],
        key=lambda n: n["rel_path"],
        reverse=True,
    )
    return JSONResponse({"daily_notes": [
        {"rel_path": n["rel_path"], "title": n["title"], "modified": n.get("modified")}
        for n in daily
    ]})


@app.get("/api/vault/tasks")
async def api_vault_tasks(path: str = "", completed: str = "") -> JSONResponse:
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    notes = list_vault_notes(path)
    results = []
    for n in notes:
        tasks = n.get("tasks") or []
        for t in tasks:
            if completed == "true" and not t.get("completed"):
                continue
            if completed == "false" and t.get("completed"):
                continue
            results.append({
                "rel_path": n["rel_path"],
                "title": n["title"],
                "text": t["text"],
                "completed": t["completed"],
                "line": t.get("line"),
            })
    return JSONResponse({"tasks": results})


@app.get("/api/vault/properties")
async def api_vault_properties(path: str = "", key: str = "", value: str = "") -> JSONResponse:
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    notes = list_vault_notes(path)
    results = []
    for n in notes:
        props = n.get("properties") or {}
        if key and key not in props:
            continue
        if key and value and props.get(key) != value:
            continue
        results.append({
            "rel_path": n["rel_path"],
            "title": n["title"],
            "properties": props,
        })
    return JSONResponse({"notes": results})


@app.get("/api/vault/search")
async def api_vault_search(path: str = "", q: str = "", limit: int = 20) -> JSONResponse:
    if not path or not q.strip():
        return JSONResponse({"results": []})
    notes = list_vault_notes(path)
    query = q.lower()
    results = []
    for n in notes:
        tags = n["tags"] if isinstance(n["tags"], list) else json.loads(n["tags"])
        searchable = f"{n['title']} {n['rel_path']} {' '.join(tags)}".lower()
        if query in searchable:
            results.append({
                "rel_path": n["rel_path"],
                "title": n["title"],
                "tags": tags,
                "word_count": n["word_count"],
            })
        if len(results) >= limit:
            break
    return JSONResponse({"results": results})


# ---------------------------------------------------------------------------
# Voice API — STT & TTS (OpenAI-compatible)
# ---------------------------------------------------------------------------

@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    model: str | None = None,
    language: str | None = None,
    prompt: str | None = None,
    response_format: str = "json",
) -> JSONResponse:
    """
    OpenAI-compatible speech-to-text endpoint.
    Accepts audio file upload, returns transcribed text.
    """
    assert _voice_backend is not None
    audio_data = await file.read()
    try:
        result = await _voice_backend.transcribe(
            audio_data=audio_data,
            language=language,
            prompt=prompt,
            response_format=response_format,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/audio/speech")
async def audio_speech(request: Request) -> Response:
    """
    OpenAI-compatible text-to-speech endpoint.
    Accepts JSON body with text, returns audio bytes.
    """
    assert _voice_backend is not None
    body = await request.json()
    text = body.get("input", "")
    voice = body.get("voice")
    speed = body.get("speed")
    response_format = body.get("response_format", "wav")

    if not text:
        return JSONResponse({"error": "input text is required"}, status_code=400)

    try:
        audio_bytes = await _voice_backend.synthesize(
            text=text,
            voice=voice,
            speed=speed,
            response_format=response_format,
        )
        media_type = "audio/wav" if response_format == "wav" else f"audio/{response_format}"
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        logger.error(f"Speech synthesis failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/voice/config")
async def api_voice_config() -> JSONResponse:
    """Return current voice configuration and model status."""
    assert _voice_backend is not None
    return JSONResponse(_voice_backend.get_voice_config())


@app.get("/api/voice/models")
async def api_voice_models() -> JSONResponse:
    """List available voice models with download status."""
    assert _voice_backend is not None
    return JSONResponse({
        "models": [m.to_dict() for m in _voice_backend.list_voice_models()]
    })


@app.post("/api/voice/chat")
async def api_voice_chat(
    file: UploadFile = File(...),
    messages: str = "",
    model_override: str | None = None,
    num_ctx: int | None = None,
) -> JSONResponse:
    """
    Full voice conversation turn: audio in → transcribe → LLM → TTS → audio out.
    Accepts multipart form with audio file and optional JSON messages history.
    Returns transcription, LLM response text, and base64-encoded audio.
    """
    assert _orchestrator is not None
    audio_data = await file.read()

    # Parse messages history from form field
    msg_list: list[dict] = []
    if messages:
        try:
            msg_list = json.loads(messages)
        except json.JSONDecodeError:
            pass

    try:
        result = await _orchestrator.handle_voice(
            audio_data=audio_data,
            messages=msg_list,
            model_override=model_override,
            num_ctx=num_ctx,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Voice chat failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


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
