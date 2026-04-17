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
from typing import Any, AsyncIterator, cast

import httpx

from .hardware_profiler import get_hardware_profile
from .model_manager import ModelManager
from .plugins.memory_plugin import MemoryPlugin
from .provenance import RetrievedMemory
from .router import Model, RouteResult, route
from .store import get_memories_context
from .tools.file_ops import file_read, file_write
from .tools.shell import shell_exec
from .tools.web_fetch import web_fetch
from .tools.web_search import format_search_results, web_search
from .voice_backend import VoiceBackend

logger = logging.getLogger(__name__)

# Matches {"tool": "...", "args": {...}} anywhere in a model response
_TOOL_CALL_RE = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*(\{[^}]*\})\s*\}',
    re.DOTALL,
)


class Orchestrator:
    def __init__(
        self,
        config: dict,
        model_manager: ModelManager,
        voice_backend: VoiceBackend | None = None,
        memory_plugin: MemoryPlugin | None = None,
    ):
        self.config = config
        self.mm = model_manager
        self._search_cfg = config.get("search", {})
        self._routing_cfg = config.get("routing", {})
        self._tools_cfg = config.get("tools", {})
        self._max_tool_calls: int = self._routing_cfg.get("max_tool_calls_per_turn", 5)
        self._hw = get_hardware_profile()
        self.voice = voice_backend
        self._memory: MemoryPlugin | None = memory_plugin

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

        # Memory injection pipeline:
        #   1. Build a recall query from the last 3 turns
        #   2. Expand broad queries into sub-queries for wider coverage
        #   3. Union-dedupe into a pool of up to 50 candidates
        #   4. Lightweight heuristic rerank to keep the 10 most relevant
        #   5. Format with char budget so we never overflow the model context
        recall_query = ""
        expanded_queries: list[str] = []
        retrieved_for_provenance: list[RetrievedMemory] = []
        if self._memory:
            recall_query = _build_recall_query(messages)
            expanded_queries = _expand_query(recall_query)
            per_query_limit = 25 if len(expanded_queries) == 1 else 15
            buckets = [
                await self._memory.recall(q, limit=per_query_limit) for q in expanded_queries
            ]
            pool = _merge_recall_results(buckets, limit=50)
            relevant = _rerank_memories(recall_query, pool, keep=10)
            mem_ctx = self._memory.format_for_prompt(relevant)
            # Snapshot the retrieved set for the provenance sidecar.
            # Index matches the 1-based [memory: N] tag the model sees.
            for i, m in enumerate(relevant, start=1):
                score = m.get("score")
                if score is None:
                    # sqlite-vec path returns `distance` (lower = better);
                    # negate so higher still means more relevant for analytics.
                    dist = m.get("distance")
                    score = -float(dist) if dist is not None else 0.0
                retrieved_for_provenance.append(RetrievedMemory(
                    index=i,
                    id=str(m.get("id", "")),
                    score=float(score),
                    content=str(m.get("content", "")),
                ))
        else:
            mem_ctx = get_memories_context()
        if mem_ctx:
            system_prompt = f"{system_prompt}\n\n{mem_ctx}"

        augmented_messages = list(messages)
        if result.search_triggered and result.search_query:
            search_ctx = await self._run_search(result.search_query, research_mode=research_mode)
            if search_ctx:
                augmented_messages = _inject_search_context(augmented_messages, search_ctx)

        full_messages = _prepend_system(augmented_messages, system_prompt)

        provenance_seed = {
            "user_query": user_message,
            "recall_query": recall_query,
            "expanded_queries": expanded_queries,
            "retrieved": [m.to_dict() for m in retrieved_for_provenance],
        }

        if stream:
            return self._stream_with_tools(
                model_name, api_base, full_messages, result,
                num_ctx=num_ctx, research_mode=research_mode,
                memory_injected=bool(mem_ctx),
                provenance_seed=provenance_seed,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            )

        return await self._call_with_tools(
            model_name, api_base, full_messages, result, num_ctx=num_ctx,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # OpenAI tools passthrough — used by agentic coding clients
    # (claw-code, Aider, Continue). The client owns the tool-use loop;
    # Loca stays out of the way but still injects grounded memory
    # context so agentic clients benefit from the knowledge hub.
    # ------------------------------------------------------------------

    async def handle_passthrough(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict | None = None,
        has_image: bool = False,
        stream: bool = False,
        model_hint: str | None = None,
        model_override: str | None = None,
        num_ctx: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
        system_prompt_override: str | None = None,
    ) -> dict | AsyncIterator[bytes]:
        user_message = _last_user_content(messages)
        result: RouteResult = route(user_message, has_image=has_image, model_hint=model_hint)
        model_name, api_base = await self.mm.ensure_loaded(
            result.model, model_name_override=model_override
        )
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            system_prompt = _build_system_prompt(result.model, model_name, self._hw)

        # Memory injection — still on, this is the differentiator vs Ollama/LM-Studio.
        if self._memory:
            recall_query = _build_recall_query(messages)
            queries = _expand_query(recall_query)
            per_query_limit = 25 if len(queries) == 1 else 15
            buckets = [
                await self._memory.recall(q, limit=per_query_limit) for q in queries
            ]
            pool = _merge_recall_results(buckets, limit=50)
            relevant = _rerank_memories(recall_query, pool, keep=10)
            mem_ctx = self._memory.format_for_prompt(relevant)
        else:
            mem_ctx = get_memories_context()
        if mem_ctx:
            system_prompt = f"{system_prompt}\n\n{mem_ctx}"

        full_messages = _prepend_system(messages, system_prompt)

        if stream:
            return self._passthrough_stream_bytes(
                model_name, api_base, full_messages, tools, tool_choice,
                num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            )

        return await self._chat(
            model_name, api_base, full_messages, stream=False,
            tools=tools, tool_choice=tool_choice,
            num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )

    async def _passthrough_stream_bytes(
        self,
        model: str,
        api_base: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict | None,
        num_ctx: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Forward backend SSE stream verbatim so tool_calls deltas reach the client."""
        base = api_base.rstrip("/")
        payload: dict = {"model": model, "messages": messages, "stream": True, "tools": tools}
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
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

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{base}/v1/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk

    # ------------------------------------------------------------------
    # Voice mode: transcribe → LLM → TTS
    # ------------------------------------------------------------------

    async def handle_voice(
        self,
        audio_data: bytes,
        messages: list[dict],
        language: str | None = None,
        model_override: str | None = None,
        num_ctx: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt_override: str | None = None,
    ) -> dict:
        """
        Full voice conversation turn:
          1. Transcribe audio → text
          2. Send text to LLM
          3. Synthesize LLM response → audio

        Returns:
          {
            "transcription": "what the user said",
            "response": "what the LLM said",
            "audio": "<base64-encoded WAV>",
            "model": "model name"
          }
        """
        if not self.voice:
            raise RuntimeError("Voice backend not configured")

        import base64

        # Step 1: Transcribe
        stt_result = await self.voice.transcribe(audio_data, language=language)
        user_text = stt_result.get("text", "").strip()

        if not user_text:
            return {
                "transcription": "",
                "response": "",
                "audio": None,
                "model": None,
            }

        # Step 2: Send to LLM
        user_msg = {"role": "user", "content": user_text}
        full_messages = list(messages) + [user_msg]

        llm_response = cast(dict, await self.handle(
            full_messages,
            stream=False,
            model_override=model_override,
            num_ctx=num_ctx,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt_override=system_prompt_override,
        ))

        response_text = _extract_content(llm_response)

        # Step 3: Synthesize response to audio
        audio_bytes = await self.voice.synthesize(response_text)
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        return {
            "transcription": user_text,
            "response": response_text,
            "audio": audio_b64,
            "model": llm_response.get("model"),
        }

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

        while tool_call_count <= self._max_tool_calls:
            response = await self._chat(
                model_name, api_base, messages, stream=False, num_ctx=num_ctx,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            )
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

        return await self._chat(
            model_name, api_base, messages, stream=False, num_ctx=num_ctx,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
        )

    async def _stream_with_tools(
        self,
        model_name: str,
        api_base: str,
        messages: list[dict],
        route_result: RouteResult,
        num_ctx: int | None = None,
        research_mode: bool = False,
        memory_injected: bool = False,
        provenance_seed: dict | None = None,
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
            "__provenance__": provenance_seed or {},
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
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
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
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
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
                    if resp.status_code == 404:
                        has_image = any(
                            isinstance(m.get("content"), list)
                            and any(p.get("type") in ("image_url", "image") for p in m["content"] if isinstance(p, dict))
                            for m in messages
                        )
                        if has_image:
                            raise Exception(
                                "The inference backend does not support image input. "
                                "This model may require mlx-vlm instead of mlx_lm for vision. "
                                "Try a text-only message or switch to a model with full vision server support."
                            )
                        raise Exception(
                            "No model is loaded. Open Manage Models and load a model before sending messages."
                        )
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
        """Store the last conversation exchange verbatim via the memory plugin."""
        if self._memory:
            return await self._store_verbatim(messages, conv_id)
        return []

    async def _store_verbatim(
        self, messages: list[dict], conv_id: str | None
    ) -> list[dict]:
        """
        Store the last user+assistant exchange verbatim.

        Storing the pair gives MemPalace full context for room classification
        (decisions, preferences, problems, etc.) rather than just the user side.
        """
        assert self._memory is not None
        saved = []

        # Find the last user message and its following assistant reply
        last_user_idx: int | None = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return []

        user_content = messages[last_user_idx].get("content", "")
        if not isinstance(user_content, str) or len(user_content.strip()) < 20:
            return []

        # Look for the immediately following assistant message
        assistant_content = ""
        if last_user_idx + 1 < len(messages):
            next_msg = messages[last_user_idx + 1]
            if next_msg.get("role") == "assistant":
                assistant_content = next_msg.get("content", "")

        if assistant_content:
            text = f"User: {user_content.strip()}\n\nAssistant: {assistant_content.strip()}"
        else:
            text = user_content.strip()

        mid = await self._memory.store(text, {"conv_id": conv_id})
        if mid is not None:
            saved.append({"id": mid, "content": text[:120] + ("..." if len(text) > 120 else ""), "type": "conversation"})
        return saved

    async def backfill_embeddings(self) -> None:
        """
        Called once after the inference backend becomes ready.
        Generates embeddings for any memories stored without one.
        """
        if self._memory and hasattr(self._memory, "backfill_embeddings"):
            await self._memory.backfill_embeddings()  # type: ignore[union-attr]

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
    # Strip filesystem path — just show the model name
    import os
    display_name = os.path.basename(model_name.rstrip("/")) if "/" in model_name else model_name
    identity = (
        f"Your name is Loca. You are a private, offline AI assistant running locally on this machine "
        f"({hw_desc}). The loaded model is {display_name}. "
        f"When asked to identify yourself, say you are Loca and mention the model and hardware. "
        f"Do not refer to any external AI company or platform as your origin.\n\n"
        f"Interpret the user's intent, not just their literal words. Users may type quickly and make "
        f"mistakes — typos, misspellings, wrong spacing, missing accents, abbreviations, slang, or "
        f"shorthand. Always infer the most likely intended meaning from context. For example: "
        f"split or joined words ('every thing', 'atleast'), phonetic spellings ('definately', 'seperate'), "
        f"missing or swapped letters ('teh', 'recieve'), informal references ('that google phone' for Pixel, "
        f"'the bird app' for Twitter/X), or partial names and acronyms. "
        f"Never give a response based on a literal misreading when the intended meaning is reasonably clear. "
        f"If genuinely ambiguous — multiple plausible interpretations exist — state what you understood "
        f"and ask the user to confirm before proceeding."
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


# Broad queries like "what do you know about me" surface too few relevant
# memories when a single embedding lookup has to cover every aspect of the
# user. Detect them and expand into sub-queries covering common facets.
_BROAD_MARKERS = (
    "about me", "about myself", "who am i", "what do you know",
    "know about me", "tell me everything", "tell me about me",
    "anything about me", "everything about me",
)
_BROAD_SHORT_TOKENS = {"me", "i", "myself"}
_EXPANDED_SUB_QUERIES = (
    "user's profession, role, and current work",
    "user's interests, hobbies, and preferences",
    "user's current projects and goals",
    "user's background, skills, and experience",
)


def _is_broad_query(query: str) -> bool:
    low = query.lower()
    if any(marker in low for marker in _BROAD_MARKERS):
        return True
    words = [w.strip("?.,!;:'\"") for w in low.split()]
    if len(words) <= 3 and any(w in _BROAD_SHORT_TOKENS for w in words):
        return True
    return False


def _expand_query(query: str) -> list[str]:
    if _is_broad_query(query):
        return [query, *_EXPANDED_SUB_QUERIES]
    return [query]


# Minimal English stopword list so "what do you know about me" doesn't inflate
# overlap against every memory that happens to contain "you".
_RERANK_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from",
    "has", "have", "i", "in", "is", "it", "its", "me", "my", "myself", "of", "on",
    "or", "she", "so", "that", "the", "this", "to", "us", "was", "we", "were",
    "what", "when", "where", "which", "who", "whom", "with", "you", "your", "am",
    "about", "tell", "know", "everything", "anything",
})
_RERANK_TOKEN_RE = re.compile(r"\w+")


def _rerank_memories(query: str, memories: list[dict], keep: int) -> list[dict]:
    """Lightweight keyword-overlap rerank over top-N recall hits.

    Not a true cross-encoder — it's a cheap, dep-free heuristic that improves
    precision on vague queries by boosting memories whose content actually
    matches query terms and penalising category-label-sized memories. A proper
    cross-encoder pass is a future upgrade (would require sentence-transformers
    + torch which conflicts with Loca's MLX-first footprint).
    """
    if keep <= 0 or not memories:
        return []
    q_tokens = {
        t for t in _RERANK_TOKEN_RE.findall(query.lower())
        if t not in _RERANK_STOPWORDS
    }
    if not q_tokens:
        return memories[:keep]
    scored: list[tuple[float, int, dict]] = []
    for rank, m in enumerate(memories):
        content = m.get("content", "")
        low = content.lower()
        m_tokens = _RERANK_TOKEN_RE.findall(low)
        if not m_tokens:
            continue
        m_set = set(m_tokens)
        overlap = len(q_tokens & m_set)
        overlap_score = overlap / len(q_tokens)
        density = overlap / len(m_tokens)
        rank_bonus = 1.0 / (1 + rank)
        length = len(content)
        if length < 20:
            length_factor = 0.3
        elif length > 3000:
            length_factor = 0.7
        else:
            length_factor = 1.0
        score = (overlap_score * 2 + density * 3 + rank_bonus) * length_factor
        scored.append((score, rank, m))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [m for _, _, m in scored[:keep]]


def _merge_recall_results(buckets: list[list[dict]], limit: int) -> list[dict]:
    """Round-robin-ish merge that dedupes by id (or content) and preserves rank."""
    seen: set[str] = set()
    merged: list[dict] = []
    for bucket in buckets:
        for item in bucket:
            key = str(item.get("id") or item.get("content", "")[:80])
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _build_recall_query(messages: list[dict]) -> str:
    """
    Build a recall query from the last 3 user+assistant messages.

    Using multiple turns gives MemPalace enough conversational thread to surface
    memories relevant to the current topic, not just the last keyword typed.
    """
    recent: list[str] = []
    for msg in messages[-6:]:  # last 6 entries covers ~3 pairs
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        recent.append(content.strip())
    return " ".join(recent)[-2000:]  # cap at 2000 chars to avoid huge queries
