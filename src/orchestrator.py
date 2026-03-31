"""
Orchestrator — the main request loop.

Flow per turn:
  1. Route the message to a mode (general/code/reason) for system prompt selection
  2. Optionally trigger web search and inject results as system context
  3. Inject memories from the memory store into the system prompt
  4. Call the inference backend via OpenAI-compatible /v1/chat/completions
  5. Parse any tool calls in the response
  6. Execute tools → inject results → re-call model (max N times)
  7. Return final response
"""

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from .hardware_profiler import get_hardware_profile
from .memory_extractor import extract_memories
from .model_manager import ModelManager
from .router import Model, RouteResult, route
from .store import add_memory, get_memories_context
from .tools.file_ops import file_read, file_write
from .tools.shell import shell_exec
from .tools.web_fetch import web_fetch
from .tools.web_search import format_search_results, web_search

logger = logging.getLogger(__name__)

# Matches {"tool": "...", "args": {...}} anywhere in a model response
_TOOL_CALL_RE = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*(\{[^}]*\})\s*\}',
    re.DOTALL,
)


class Orchestrator:
    def __init__(self, config: dict, model_manager: ModelManager):
        self.config = config
        self.mm = model_manager
        self._search_cfg = config.get("search", {})
        self._routing_cfg = config.get("routing", {})
        self._tools_cfg = config.get("tools", {})
        self._max_tool_calls: int = self._routing_cfg.get("max_tool_calls_per_turn", 5)
        self._hw = get_hardware_profile()

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def handle(
        self,
        messages: list[dict],
        has_image: bool = False,
        stream: bool = False,
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
    ) -> dict | AsyncIterator[str | dict]:
        user_message = _last_user_content(messages)
        result: RouteResult = route(user_message, has_image=has_image, model_hint=model_hint)
        logger.info(
            f"Route: {result.model.value} | {result.reason} | search={result.search_triggered}"
            + (f" | override={model_override}" if model_override else "")
        )

        model_name, api_base = await self.mm.ensure_loaded(result.model, model_name_override=model_override)
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            system_prompt = _build_system_prompt(result.model, model_name, self._hw)
        mem_ctx = get_memories_context()
        if mem_ctx:
            system_prompt = f"{system_prompt}\n\n{mem_ctx}"

        augmented_messages = list(messages)
        if result.search_triggered and result.search_query:
            search_ctx = await self._run_search(result.search_query, research_mode=research_mode)
            if search_ctx:
                augmented_messages = _inject_search_context(augmented_messages, search_ctx)

        full_messages = _prepend_system(augmented_messages, system_prompt)

        inf_kwargs = dict(
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )

        if stream:
            return self._stream_with_tools(
                model_name, api_base, full_messages, result,
                num_ctx=num_ctx, research_mode=research_mode,
                memory_injected=bool(mem_ctx), **inf_kwargs,
            )

        return await self._call_with_tools(
            model_name, api_base, full_messages, result, num_ctx=num_ctx, **inf_kwargs,
        )

    # ------------------------------------------------------------------
    # Internal: model calls + tool loop
    # ------------------------------------------------------------------

    async def _call_with_tools(
        self,
        model_name: str,
        api_base: str,
        messages: list[dict],
        route_result: RouteResult,
        num_ctx: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Call model, handle tool calls, return final response."""
        tool_call_count = 0
        inf_kwargs = dict(
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )

        while tool_call_count <= self._max_tool_calls:
            response = await self._chat(model_name, api_base, messages, stream=False, num_ctx=num_ctx, **inf_kwargs)
            assistant_text: str = _extract_content(response)

            tool_call = _extract_tool_call(assistant_text)
            if tool_call is None or tool_call_count >= self._max_tool_calls:
                return response

            tool_name, tool_args = tool_call
            logger.info(f"Tool call [{tool_call_count + 1}/{self._max_tool_calls}]: {tool_name}({tool_args})")

            tool_result = await self._execute_tool(tool_name, tool_args)
            tool_result_text = json.dumps(tool_result, ensure_ascii=False)

            messages = messages + [
                {"role": "assistant", "content": assistant_text},
                {"role": "user", "content": f"<tool_result tool=\"{tool_name}\">\n{tool_result_text}\n</tool_result>"},
            ]
            tool_call_count += 1

        return await self._chat(model_name, api_base, messages, stream=False, num_ctx=num_ctx, **inf_kwargs)

    async def _stream_with_tools(
        self,
        model_name: str,
        api_base: str,
        messages: list[dict],
        route_result: RouteResult,
        num_ctx: int | None = None,
        research_mode: bool = False,
        memory_injected: bool = False,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Streaming: collects full response (for tool-call simplicity), yields in chunks.
        Yields a metadata dict first so the proxy can forward the actual model name."""
        response = await self._call_with_tools(
            model_name, api_base, messages, route_result, num_ctx=num_ctx,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )
        actual_model = response.get("model", model_name)
        yield {
            "__model__": actual_model,
            "__search__": route_result.search_triggered,
            "__memory__": memory_injected,
        }
        content = _extract_content(response)
        chunk_size = 50
        for i in range(0, len(content), chunk_size):
            yield content[i: i + chunk_size]

    async def _chat(
        self,
        model: str,
        api_base: str,
        messages: list[dict],
        stream: bool = False,
        num_ctx: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Call an OpenAI-compatible /v1/chat/completions endpoint with retry on 400/503."""
        base = api_base.rstrip("/")
        payload: dict = {"model": model, "messages": messages, "stream": stream}
        if num_ctx:
            payload["num_ctx"] = num_ctx
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if top_k is not None:
            payload["top_k"] = top_k
        if repeat_penalty is not None:
            payload["repeat_penalty"] = repeat_penalty
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        last_exc: Exception | None = None

        for attempt in range(3):
            if attempt:
                await asyncio.sleep(3 * attempt)  # 3 s, 6 s
            async with httpx.AsyncClient(timeout=300.0) as client:
                try:
                    resp = await client.post(f"{base}/v1/chat/completions", json=payload)
                    if resp.status_code in (400, 503) and attempt < 2:
                        logger.warning(
                            f"Backend {resp.status_code} on attempt {attempt + 1}, "
                            "retrying (model may still be loading)..."
                        )
                        last_exc = httpx.HTTPStatusError(
                            f"{resp.status_code}", request=resp.request, response=resp
                        )
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (400, 503) and attempt < 2:
                        logger.warning(f"Backend {e.response.status_code} on attempt {attempt + 1}, retrying...")
                        last_exc = e
                        continue
                    raise

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Tool execution dispatcher
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_name: str, args: dict) -> Any:
        match tool_name:
            case "web_search":
                results = await web_search(
                    query=args.get("query", ""),
                    searxng_url=self._search_cfg.get("searxng_url", ""),
                    max_results=self._search_cfg.get("max_results", 5),
                    max_tokens_per_result=self._search_cfg.get("max_tokens_per_result", 500),
                )
                return format_search_results(results)

            case "web_fetch":
                return await web_fetch(url=args.get("url", ""))

            case "file_read":
                return file_read(path=args.get("path", ""))

            case "file_write":
                return file_write(
                    path=args.get("path", ""),
                    content=args.get("content", ""),
                    overwrite=args.get("overwrite", True),
                )

            case "shell_exec":
                shell_cfg = self._tools_cfg.get("shell_exec", {})
                if not shell_cfg.get("enabled", True):
                    return {"error": "shell_exec is disabled in config"}
                allowed = set(shell_cfg.get("allowed_commands", []))
                timeout = shell_cfg.get("timeout_seconds", 30)
                return await shell_exec(
                    command=args.get("command", ""),
                    allowed_commands=allowed or None,
                    timeout_seconds=timeout,
                )

            case "image_describe":
                image_path = args.get("path", "")
                general_name = await self.mm.get_model_name(Model.GENERAL)
                general_base = await self.mm.get_model_api_base(Model.GENERAL)
                prompt = args.get("prompt", "Describe this image in detail.")
                image_url = image_path if image_path.startswith(("http", "data:")) else f"file://{image_path}"
                vision_messages = [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}]
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        f"{general_base.rstrip('/')}/v1/chat/completions",
                        json={"model": general_name, "messages": vision_messages, "stream": False},
                    )
                    resp.raise_for_status()
                    return {"description": _extract_content(resp.json())}

            case _:
                return {"error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # Memory extraction (called from proxy after conversation turns)
    # ------------------------------------------------------------------

    async def extract_and_save_memories(
        self, messages: list[dict], conv_id: str | None = None
    ) -> list[dict]:
        """Run three-pass memory extraction, persist results, return saved list."""
        model_name, api_base = await self.mm.ensure_loaded(Model.GENERAL)
        extracted = await extract_memories(messages, model_name, api_base)
        saved = []
        for mem_type, facts in extracted.items():
            for fact in facts:
                mid = add_memory(fact, conv_id=conv_id, type=mem_type)
                saved.append({"id": mid, "content": fact, "type": mem_type})
        return saved

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _run_search(self, query: str, research_mode: bool = False) -> str:
        searxng_url = self._search_cfg.get("searxng_url", "")
        if not searxng_url:
            logger.warning("Search triggered but searxng_url not configured")
            return ""
        if research_mode:
            logger.info("Research mode ON — using Playwright for content extraction")
        results = await web_search(
            query=query,
            searxng_url=searxng_url,
            max_results=self._search_cfg.get("max_results", 5),
            max_tokens_per_result=self._search_cfg.get("max_tokens_per_result", 500),
            research_mode=research_mode,
        )
        return format_search_results(results)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _last_user_content(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            return str(content)
    return ""


def _build_system_prompt(model: Model, model_name: str, hw) -> str:
    base = _load_system_prompt(model)
    # Build a concise hardware description from real profiler data
    if hw.has_apple_silicon:
        hw_desc = f"{hw.cpu_name}, {hw.total_ram_gb:.0f} GB unified memory"
    elif hw.has_nvidia_gpu:
        hw_desc = f"{hw.cpu_name}, {hw.total_ram_gb:.0f} GB RAM (NVIDIA GPU)"
    else:
        hw_desc = f"{hw.cpu_name}, {hw.total_ram_gb:.0f} GB RAM"
    identity = (
        f"Your name is Loca. You are a private, offline AI assistant running locally on this machine "
        f"({hw_desc}). The loaded model is {model_name}. "
        f"When asked to identify yourself, say you are Loca and mention the model and hardware. "
        f"Do not refer to any external AI company or platform as your origin."
    )
    return f"{identity}\n\n{base}"


def _load_system_prompt(model: Model) -> str:
    import os
    prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
    filename = {
        Model.GENERAL: "system_general.md",
        Model.REASON: "system_reason.md",
        Model.CODE: "system_code.md",
    }[model]
    path = os.path.join(prompts_dir, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "You are a helpful assistant."


def _inject_search_context(messages: list[dict], search_ctx: str) -> list[dict]:
    """Prepend search results to the last user message."""
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            existing = result[i].get("content", "")
            if isinstance(existing, list):
                result[i] = {**result[i], "content": existing + [{"type": "text", "text": search_ctx}]}
            else:
                result[i] = {**result[i], "content": f"{search_ctx}\n\n{existing}"}
            break
    return result


def _prepend_system(messages: list[dict], system_prompt: str) -> list[dict]:
    """Add or replace the system message at position 0."""
    if messages and messages[0].get("role") == "system":
        return [{"role": "system", "content": system_prompt}] + messages[1:]
    return [{"role": "system", "content": system_prompt}] + messages


def _extract_content(response: dict) -> str:
    """Pull message content from an OpenAI-compatible /v1/chat/completions response."""
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        return ""


def _extract_tool_call(text: str) -> tuple[str, dict] | None:
    """Parse the first tool call JSON from the model's response text."""
    m = _TOOL_CALL_RE.search(text)
    if not m:
        return None
    tool_name = m.group(1)
    try:
        args = json.loads(m.group(2))
    except json.JSONDecodeError:
        args = {}
    return tool_name, args
