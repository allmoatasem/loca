"""
Orchestrator — the main request loop.

Flow per turn:
  1. Route the message to a model
  2. Optionally trigger web search and inject results as system context
  3. Call the model via LM Studio's OpenAI-compatible API (localhost:1234/v1)
  4. Parse any tool calls in the response
  5. Execute tools → inject results → re-call model (max N times)
  6. Return final response
"""

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from .model_manager import ModelManager
from .router import Model, RouteResult, route
from .tools.web_search import format_search_results, web_search
from .tools.web_fetch import web_fetch
from .tools.file_ops import file_read, file_write
from .tools.shell import shell_exec

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
        self._lmstudio_base: str = config.get("proxy", {}).get("lmstudio_base_url", "http://localhost:1234")

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def handle(
        self,
        messages: list[dict],
        has_image: bool = False,
        stream: bool = False,
    ) -> dict | AsyncIterator[str]:
        """
        Handle a full conversation turn.

        Args:
            messages:  OpenAI-style message list. Last message is the user turn.
            has_image: Whether the request contains an image attachment.
            stream:    If True, returns an async generator of text chunks.

        Returns:
            OpenAI-compatible /v1/chat/completions response dict (or async generator if stream=True).
        """
        user_message = _last_user_content(messages)
        result: RouteResult = route(user_message, has_image=has_image)
        logger.info(f"Route: {result.model.value} | {result.reason} | search={result.search_triggered}")

        model_name = await self.mm.ensure_loaded(result.model)
        system_prompt = _load_system_prompt(result.model)

        # Inject search results if triggered
        augmented_messages = list(messages)
        if result.search_triggered and result.search_query:
            search_ctx = await self._run_search(result.search_query)
            if search_ctx:
                augmented_messages = _inject_search_context(augmented_messages, search_ctx)

        # Build final message list with system prompt
        full_messages = _prepend_system(augmented_messages, system_prompt)

        if stream:
            return self._stream_with_tools(model_name, full_messages, result)

        return await self._call_with_tools(model_name, full_messages, result)

    # ------------------------------------------------------------------
    # Internal: model calls + tool loop
    # ------------------------------------------------------------------

    async def _call_with_tools(
        self,
        model_name: str,
        messages: list[dict],
        route_result: RouteResult,
    ) -> dict:
        """Call model, handle tool calls, return final response."""
        tool_call_count = 0

        while tool_call_count <= self._max_tool_calls:
            response = await self._lmstudio_chat(model_name, messages, stream=False)
            assistant_text: str = _extract_content(response)

            tool_call = _extract_tool_call(assistant_text)
            if tool_call is None or tool_call_count >= self._max_tool_calls:
                return response

            tool_name, tool_args = tool_call
            logger.info(f"Tool call [{tool_call_count + 1}/{self._max_tool_calls}]: {tool_name}({tool_args})")

            tool_result = await self._execute_tool(tool_name, tool_args)
            tool_result_text = json.dumps(tool_result, ensure_ascii=False)

            # Append assistant turn + tool result as a user message
            messages = messages + [
                {"role": "assistant", "content": assistant_text},
                {"role": "user", "content": f"<tool_result tool=\"{tool_name}\">\n{tool_result_text}\n</tool_result>"},
            ]
            tool_call_count += 1

        return await self._lmstudio_chat(model_name, messages, stream=False)

    async def _stream_with_tools(
        self,
        model_name: str,
        messages: list[dict],
        route_result: RouteResult,
    ) -> AsyncIterator[str]:
        """Streaming version: yields text chunks, handles tool calls mid-stream."""
        # Collect the full response first (keeps tool-call logic simple),
        # then stream it in chunks.
        response = await self._call_with_tools(model_name, messages, route_result)
        content = _extract_content(response)
        # Yield in chunks of 50 chars to simulate streaming
        chunk_size = 50
        for i in range(0, len(content), chunk_size):
            yield content[i: i + chunk_size]

    async def _lmstudio_chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = False,
    ) -> dict:
        """Call LM Studio's OpenAI-compatible /v1/chat/completions endpoint."""
        payload = {"model": model, "messages": messages, "stream": stream}
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self._lmstudio_base}/v1/chat/completions", json=payload
            )
            resp.raise_for_status()
            return resp.json()

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
                # Route to general (vision-capable) model using OpenAI image_url format
                image_path = args.get("path", "")
                general_name = await self.mm.get_model_name(Model.GENERAL)
                prompt = args.get("prompt", "Describe this image in detail.")
                # Build base64 data URL if given a file path, else assume it's already a URL/data URI
                image_url = image_path if image_path.startswith(("http", "data:")) else f"file://{image_path}"
                vision_messages = [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}]
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        f"{self._lmstudio_base}/v1/chat/completions",
                        json={"model": general_name, "messages": vision_messages, "stream": False},
                    )
                    resp.raise_for_status()
                    return {"description": _extract_content(resp.json())}

            case _:
                return {"error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _run_search(self, query: str) -> str:
        searxng_url = self._search_cfg.get("searxng_url", "")
        if not searxng_url:
            logger.warning("Search triggered but searxng_url not configured")
            return ""
        results = await web_search(
            query=query,
            searxng_url=searxng_url,
            max_results=self._search_cfg.get("max_results", 5),
            max_tokens_per_result=self._search_cfg.get("max_tokens_per_result", 500),
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
                # multi-part (text + images)
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            return str(content)
    return ""


def _load_system_prompt(model: Model) -> str:
    import os
    prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
    filename = {
        Model.GENERAL: "system_general.md",
        Model.REASON: "system_reason.md",
        Model.CODE: "system_code.md",
        Model.WRITE: "system_write.md",
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
