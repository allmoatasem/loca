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
import os
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
from .tools.web_search import SearchResult, format_search_results, web_search
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
        conv_id: str | None = None,
        partner_mode: str | None = None,
        project_id: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        max_tokens: int | None = None,
        system_prompt_override: str | None = None,
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
    ) -> dict | AsyncIterator[str | dict]:
        user_message = _last_user_content(messages)
        result: RouteResult = route(user_message, has_image=has_image, model_hint=model_hint)
        logger.info(
            f"Route: {result.model.value} | {result.reason} | search={result.search_triggered}"
            + (f" | override={model_override}" if model_override else "")
            + (f" | partner={partner_mode}" if partner_mode else "")
            + (f" | project={project_id}" if project_id else "")
            + (" | deep_dive" if research_mode else "")
        )

        model_name, api_base = await self.mm.ensure_loaded(result.model, model_name_override=model_override)

        # Deep Dive = autonomous multi-role loop + Playwright web fetch.
        # Agent and Deep Dive used to be separate toggles; consolidated
        # into this single flag in omnibus #92 — they were effectively
        # augmenting each other. When `research_mode` is True we:
        #   (a) hand the whole turn to the loop (Researcher → Reviewer →
        #       Writer → Verifier), and
        #   (b) the loop's `web_search` calls go through Playwright so
        #       each source hit has full-page content, not snippets.
        # When False: standard single model call + snippet-only search.
        if research_mode:
            return self._stream_autonomous_loop(
                messages=messages,
                user_message=user_message,
                model_name=model_name,
                api_base=api_base,
                conv_id=conv_id or "no-conv-id",
                project_id=project_id,
                num_ctx=num_ctx,
                temperature=temperature,
                max_tokens=max_tokens,
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
            )
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            system_prompt = _build_system_prompt(
                result.model, model_name, self._hw,
                partner_mode=partner_mode, project_id=project_id,
            )

        # Memory injection pipeline:
        #   1. Build a recall query from the last 3 turns
        #   2. Expand broad queries into sub-queries for wider coverage
        #   3. Union-dedupe into a pool of up to 50 candidates
        #   4. Lightweight heuristic rerank to keep the 10 most relevant
        #   5. Format with char budget so we never overflow the model context
        recall_query = ""
        expanded_queries: list[str] = []
        retrieved_for_provenance: list[RetrievedMemory] = []
        skipped_meta_query = _is_meta_query(user_message)
        if self._memory and not skipped_meta_query:
            recall_query = _build_recall_query(messages)
            expanded_queries = _expand_query(recall_query)
            per_query_limit = 25 if len(expanded_queries) == 1 else 15
            buckets = [
                await self._memory.recall(q, limit=per_query_limit) for q in expanded_queries
            ]
            pool = _merge_recall_results(buckets, limit=50)
            # Research Partner: prepend the project's bookmarked items to
            # the pool so rerank's `rank_bonus` favours user-curated
            # sources over generic vault matches.
            if project_id:
                project_boost = _project_items_as_memories(project_id)
                obsidian_boost = _obsidian_source_as_memories(
                    project_id, recall_query, limit=10,
                )
                pool = project_boost + obsidian_boost + pool
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
        elif skipped_meta_query:
            logger.info("Skipping memory recall: meta-query detected in user message")
            mem_ctx = ""
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
            "skipped_meta_query": skipped_meta_query,
        }

        if stream:
            return self._stream_with_tools(
                model_name, api_base, full_messages, result,
                num_ctx=num_ctx, research_mode=research_mode,
                memory_injected=bool(mem_ctx),
                provenance_seed=provenance_seed,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
            )

        return await self._call_with_tools(
            model_name, api_base, full_messages, result, num_ctx=num_ctx,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
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
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
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
        if self._memory and not _is_meta_query(user_message):
            recall_query = _build_recall_query(messages)
            queries = _expand_query(recall_query)
            per_query_limit = 25 if len(queries) == 1 else 15
            buckets = [
                await self._memory.recall(q, limit=per_query_limit) for q in queries
            ]
            pool = _merge_recall_results(buckets, limit=50)
            relevant = _rerank_memories(recall_query, pool, keep=10)
            mem_ctx = self._memory.format_for_prompt(relevant)
        elif self._memory:
            logger.info("Skipping memory recall: meta-query detected in user message")
            mem_ctx = ""
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
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
            )

        return await self._chat(
            model_name, api_base, full_messages, stream=False,
            tools=tools, tool_choice=tool_choice,
            num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
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
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
    ) -> AsyncIterator[bytes]:
        """Forward backend SSE stream verbatim so tool_calls deltas reach the client."""
        base = api_base.rstrip("/")
        payload: dict = {"model": model, "messages": messages, "stream": True, "tools": tools}
        if extra_body:
            for k, v in extra_body.items():
                if k not in ("model", "messages", "stream", "tools"):
                    payload[k] = v
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs
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
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
    ) -> dict:
        """Call model, handle tool calls, return final response."""
        tool_call_count = 0

        while tool_call_count <= self._max_tool_calls:
            response = await self._chat(
                model_name, api_base, messages, stream=False, num_ctx=num_ctx,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, max_tokens=max_tokens,
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
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
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
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
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Streaming: collects full response (for tool-call simplicity), yields in chunks.
        Yields a metadata dict first so the proxy can forward the actual model name."""
        response = await self._call_with_tools(
            model_name, api_base, messages, route_result, num_ctx=num_ctx,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, max_tokens=max_tokens,
            chat_template_kwargs=chat_template_kwargs,
            extra_body=extra_body,
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

    async def _stream_autonomous_loop(
        self,
        *,
        messages: list[dict],
        user_message: str,
        model_name: str,
        api_base: str,
        conv_id: str,
        project_id: str | None,
        num_ctx: int | None,
        temperature: float | None,
        max_tokens: int | None,
        chat_template_kwargs: dict | None,
        extra_body: dict | None,
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Route a single turn through the multi-role research loop.

        The loop module does the planning / searching / synthesis; this
        wrapper adapts its yields into the same `{__meta__} → chunks`
        shape `_stream_with_tools` emits, so the proxy's streaming code
        doesn't need a special case.
        """
        from .research_loop import LoopSource, run_research_loop  # noqa: PLC0415

        # Pull project-scoped memory recall so the loop's Writer has the
        # same context the regular turn would. Intentionally lighter than
        # the main `handle()` pipeline — no query expansion, no rerank —
        # because the Researcher is also going to gather fresh web
        # sources, and spending three extra LLM calls on recall shaping
        # burns tokens for diminishing returns.
        memory_sources: list[LoopSource] = []
        if self._memory:
            try:
                pool = await self._memory.recall(user_message, limit=20)
                if project_id:
                    project_boost = _project_items_as_memories(project_id)
                    obsidian_boost = _obsidian_source_as_memories(
                        project_id, user_message, limit=10,
                    )
                    pool = project_boost + obsidian_boost + pool
                for i, m in enumerate(pool[:10], start=1):
                    memory_sources.append(LoopSource(
                        idx=i, origin="memory",
                        title=(m.get("content") or "")[:60],
                        snippet=str(m.get("content") or ""),
                    ))
            except Exception as exc:
                logger.warning("loop: memory recall failed, continuing without: %s", exc)

        # Closure around `_chat` + `web_search` with the per-turn params
        # baked in. Keeps the loop module dependency-free for testing.
        async def _chat_fn(*, messages: list[dict], **kwargs: Any) -> dict:
            return await self._chat(
                model=model_name,
                api_base=api_base,
                messages=messages,
                num_ctx=num_ctx,
                chat_template_kwargs=chat_template_kwargs,
                extra_body=extra_body,
                **kwargs,
            )

        async def _search_fn(*, query: str, max_results: int = 5) -> list[SearchResult]:
            # research_mode=True: Playwright fetches the full page for
            # every hit instead of trafilatura snippets. Deep Dive = loop
            # + full-page content, consolidated into one flag in #92.
            return await web_search(
                query=query,
                searxng_url=self._search_cfg.get("searxng_url", ""),
                max_results=max_results,
                max_tokens_per_result=self._search_cfg.get("max_tokens_per_result", 500),
                research_mode=True,
            )

        # Metadata dict first — the proxy reads `__model__` etc. and the
        # UI's stats bar needs it even on loop turns.
        yield {
            "__model__": model_name,
            "__search__": True,  # loops always hit the web
            "__memory__": bool(memory_sources),
            "__provenance__": {
                "conv_id": conv_id,
                "autonomous_loop": True,
                "memory_count": len(memory_sources),
            },
        }

        # Stream through the loop. Each yield is a string; we chunk-
        # forward it so the SSE renders in consistent 50-char pieces.
        chunk_size = 50
        async for piece in run_research_loop(
            chat_fn=_chat_fn,
            search_fn=_search_fn,
            user_query=user_message,
            history=messages,
            memory_sources=memory_sources,
            conv_id=conv_id,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            for i in range(0, len(piece), chunk_size):
                yield piece[i: i + chunk_size]

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
        chat_template_kwargs: dict | None = None,
        extra_body: dict | None = None,
    ) -> dict:
        """Call an OpenAI-compatible /v1/chat/completions endpoint with retry on 400/503.

        `chat_template_kwargs` forwards Jinja-template vars the model's chat
        template reads (e.g. `enable_thinking`, `preserve_thinking` on Qwen3).

        `extra_body` is the OpenAI-SDK convention: arbitrary top-level fields
        the backend understands but Loca doesn't model explicitly (min_p,
        mirostat_*, xtc_*, dry_*, grammar, …). Shallow-merged into the
        payload; explicit Loca fields win on key collision so the UI's
        temperature/top_p/top_k/etc. can't be silently overridden.
        """
        base = api_base.rstrip("/")
        payload: dict = {"model": model, "messages": messages, "stream": stream}
        # Apply extra_body FIRST so explicit Loca fields below can overwrite
        # any accidentally-colliding keys.
        if extra_body:
            for k, v in extra_body.items():
                if k not in ("model", "messages", "stream"):
                    payload[k] = v
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
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs
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


def _build_system_prompt(
    model: Model,
    model_name: str,
    hw,
    *,
    partner_mode: str | None = None,
    project_id: str | None = None,
) -> str:
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
    parts = [identity, base]

    # Research Partner — optional project scope + partner-mode overlay.
    # Both are additive: scope prepends the user's research context, and
    # partner_mode ("critique" | "teach") layers a behavioural instruction
    # set on top of the base prompt without replacing it.
    if project_id:
        scope_block = _load_project_scope_block(project_id)
        if scope_block:
            parts.append(scope_block)
    if partner_mode in ("critique", "teach"):
        mode_block = _load_partner_mode_prompt(partner_mode)
        if mode_block:
            parts.append(mode_block)

    return "\n\n".join(parts)


def _load_project_scope_block(project_id: str) -> str:
    try:
        from .store import get_project  # noqa: PLC0415
        proj = get_project(project_id)
    except Exception:  # pragma: no cover — store failures shouldn't break chat
        return ""
    if not proj:
        return ""
    title = (proj.get("title") or "").strip()
    scope = (proj.get("scope") or "").strip()
    if not scope:
        return ""
    return (
        f"## Active research project — {title}\n\n"
        f"The user is working inside a scoped research project. The project's "
        f"stated scope is:\n\n> {scope}\n\n"
        f"Bias your answers toward this scope. If the user drifts off-topic, "
        f"answer the question but gently surface the connection back to the "
        f"project when one exists."
    )


def _load_partner_mode_prompt(mode: str) -> str:
    filename = {"critique": "system_critique.md", "teach": "system_teach.md"}.get(mode)
    if not filename:
        return ""
    import os  # noqa: PLC0415
    prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
    path = os.path.join(prompts_dir, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:  # pragma: no cover
        return ""


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
#
# Strong markers fire on their own — the phrase itself targets the user's
# own knowledge base. Soft markers require a personal pronoun nearby, so
# "what do you know about FastAPI" doesn't get fanned into user-facet
# sub-queries (that happened pre-PR: rerank then fought with irrelevant
# personal memories and polluted the answer).
_BROAD_STRONG_MARKERS = (
    "about me", "about myself", "who am i",
    "know about me", "tell me everything", "tell me about me",
    "anything about me", "everything about me",
)
_BROAD_SOFT_MARKERS = ("what do you know", "tell me what you know")
_BROAD_PERSONAL_TOKENS = frozenset({"me", "my", "myself", "i"})
_BROAD_SHORT_TOKENS = {"me", "i", "myself"}
_EXPANDED_SUB_QUERIES = (
    "user's profession, role, and current work",
    "user's interests, hobbies, and preferences",
    "user's current projects and goals",
    "user's background, skills, and experience",
)


def _tokenise(query: str) -> list[str]:
    return [w.strip("?.,!;:'\"") for w in query.lower().split()]


def _is_broad_query(query: str) -> bool:
    low = query.lower()
    if any(marker in low for marker in _BROAD_STRONG_MARKERS):
        return True
    words = _tokenise(query)
    token_set = set(words)
    if any(marker in low for marker in _BROAD_SOFT_MARKERS) and (token_set & _BROAD_PERSONAL_TOKENS):
        return True
    if len(words) <= 3 and any(w in _BROAD_SHORT_TOKENS for w in words):
        return True
    return False


def _expand_query(query: str) -> list[str]:
    if _is_broad_query(query):
        return [query, *_EXPANDED_SUB_QUERIES]
    return [query]


# Phrases that unambiguously ask the model to reason from its pretraining
# weights instead of from Loca's injected memory context. When the user
# asks "what do you know about X from your training data", a literal
# bigram match on "training data" in the vault pulls in ML-textbook
# chunks that hijack the answer; skipping recall for the turn lets the
# model fall back on parametric knowledge as requested.
_META_QUERY_MARKERS = (
    "training data",
    "pre-training",
    "pretraining",
    "pre-train",
    "pretrain",
    "parametric",
    "model's weights",
    "your weights",
    "model knowledge",
    "your model's knowledge",
    "what you learned during training",
    "what you were trained on",
)


def _is_meta_query(query: str) -> bool:
    """True when the query asks the model to reason from pretraining
    knowledge rather than injected memory. Retrieval is skipped for the
    turn so vault chunks matching the literal meta-phrase don't hijack
    the answer.
    """
    return any(marker in query.lower() for marker in _META_QUERY_MARKERS)


def _project_items_as_memories(project_id: str) -> list[dict]:
    """Project-scoped bookmarks, shaped like recall hits so they can flow
    through `_merge_recall_results` + `_rerank_memories` unchanged."""
    try:
        from .store import list_project_items  # noqa: PLC0415
        items = list_project_items(project_id, limit=50)
    except Exception:  # pragma: no cover — store failures shouldn't break chat
        return []
    out: list[dict] = []
    for item in items:
        parts: list[str] = []
        title = (item.get("title") or "").strip()
        body = (item.get("body") or "").strip()
        url = (item.get("url") or "").strip()
        if title:
            parts.append(title)
        if body:
            parts.append(body)
        if url:
            parts.append(f"(source: {url})")
        content = " — ".join(parts)
        if not content:
            continue
        out.append({
            "id": f"project_item:{item['id']}",
            "content": content,
            "score": 1.0,
            "type": "project_item",
        })
    return out


def _obsidian_source_as_memories(
    project_id: str, recall_query: str, limit: int = 10,
) -> list[dict]:
    """If the project opts into `obsidian_source`, run a semantic query
    over every watched vault and reshape the top notes as recall hits.

    This is how "attach the Obsidian vault without re-ingesting" works:
    the shared `vault_notes` index populated by the background watcher
    is queried live per turn. No per-project bookmarking, no duplicate
    storage — just in-flight retrieval.
    """
    if not recall_query.strip():
        return []
    try:
        from .store import get_project  # noqa: PLC0415
        project = get_project(project_id)
    except Exception:  # pragma: no cover
        return []
    if not project or not project.get("obsidian_source"):
        return []
    try:
        from .obsidian_watcher import search_watched_vaults  # noqa: PLC0415
        hits = search_watched_vaults(recall_query, limit=limit)
    except Exception:  # pragma: no cover
        return []
    out: list[dict] = []
    for h in hits:
        title = (h.get("title") or "").strip()
        rel = (h.get("rel_path") or "").strip()
        snippet = (h.get("snippet") or "").strip()
        parts = [p for p in (title, snippet) if p]
        content = " — ".join(parts)
        if not content:
            continue
        out.append({
            "id": f"obsidian:{h.get('vault_path', '')}:{rel}",
            "content": content,
            "score": float(h.get("score") or 0.0),
            "type": "obsidian_note",
        })
    return out


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

# Minimum content-token overlap with the query for a memory to survive
# rerank. With overlap = 0 the only score contribution is `rank_bonus` —
# a recall-ordering position bump — which lets unrelated chunks get
# injected when the vault simply has nothing relevant to the query.
# Grounding discipline (`[memory: N]` citations) then binds the model to
# those noise chunks. Env override exists for experimentation.
_MIN_RERANK_OVERLAP = int(os.environ.get("LOCA_MIN_RERANK_OVERLAP", "1"))


def _rerank_memories(query: str, memories: list[dict], keep: int) -> list[dict]:
    """Lightweight keyword-overlap rerank over top-N recall hits.

    Not a true cross-encoder — it's a cheap, dep-free heuristic that improves
    precision on vague queries by boosting memories whose content actually
    matches query terms and penalising category-label-sized memories. A proper
    cross-encoder pass is a future upgrade (would require sentence-transformers
    + torch which conflicts with Loca's MLX-first footprint).

    Applies a relevance floor (`_MIN_RERANK_OVERLAP`): when the query has
    meaningful tokens, memories with zero overlap are dropped rather than
    kept-by-rank. Prevents "least-bad-irrelevant" chunks from being cited
    when nothing in the vault actually matches.
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
        if overlap < _MIN_RERANK_OVERLAP:
            continue
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
