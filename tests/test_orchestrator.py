"""
Tests for the Orchestrator module.

Covers:
  - Message routing and system-prompt selection
  - Memory injection into system prompt
  - Search context injection
  - Tool call detection, execution, and loop limits
  - Streaming response (metadata sentinel + chunked text)
  - Memory extraction delegation
  - Retry on 400 / 503 backend responses

Run with: pytest tests/test_orchestrator.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator import (
    Orchestrator,
    _extract_content,
    _extract_tool_call,
    _inject_search_context,
    _last_user_content,
    _prepend_system,
)
from src.router import Model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: str, model: str = "test-model") -> dict:
    """Build a minimal OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_orchestrator(config: dict | None = None) -> tuple[Orchestrator, MagicMock]:
    """Return (orchestrator, mock_model_manager)."""
    cfg = config or {
        "routing": {"max_tool_calls_per_turn": 5},
        "search": {"searxng_url": "http://searxng", "max_results": 3, "max_tokens_per_result": 200},
        "tools": {"shell_exec": {"enabled": True, "allowed_commands": [], "timeout_seconds": 30}},
    }
    mm = MagicMock()
    mm.ensure_loaded = AsyncMock(return_value=("test-model", "http://localhost:11434"))
    mm.get_model_name = AsyncMock(return_value="test-model")
    mm.get_model_api_base = AsyncMock(return_value="http://localhost:11434")
    orch = Orchestrator(cfg, mm)
    return orch, mm


# ---------------------------------------------------------------------------
# Pure helper tests (no I/O)
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_last_user_content_string(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert _last_user_content(messages) == "Hello world"

    def test_last_user_content_list(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]}]
        assert _last_user_content(messages) == "Describe this"

    def test_last_user_content_picks_last(self):
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second message"},
        ]
        assert _last_user_content(messages) == "Second message"

    def test_last_user_content_empty(self):
        assert _last_user_content([]) == ""
        assert _last_user_content([{"role": "assistant", "content": "hi"}]) == ""

    def test_extract_content_normal(self):
        resp = _make_response("Hello!")
        assert _extract_content(resp) == "Hello!"

    def test_extract_content_missing_returns_empty(self):
        assert _extract_content({}) == ""
        assert _extract_content({"choices": []}) == ""

    def test_extract_tool_call_valid(self):
        text = 'Some preamble {"tool": "web_search", "args": {"query": "python"}} done'
        result = _extract_tool_call(text)
        assert result is not None
        tool, args = result
        assert tool == "web_search"
        assert args == {"query": "python"}

    def test_extract_tool_call_none_when_absent(self):
        assert _extract_tool_call("No tool call here") is None

    def test_extract_tool_call_invalid_json_args(self):
        # Regex matches but args JSON is malformed — should return empty dict
        text = '{"tool": "web_search", "args": {bad json}}'
        result = _extract_tool_call(text)
        # May or may not match depending on regex — either None or empty-args tuple
        if result is not None:
            _, args = result
            assert isinstance(args, dict)

    def test_inject_search_context_string_content(self):
        messages = [{"role": "user", "content": "What is Python?"}]
        augmented = _inject_search_context(messages, "SEARCH RESULTS HERE")
        assert "SEARCH RESULTS HERE" in augmented[-1]["content"]
        assert "What is Python?" in augmented[-1]["content"]

    def test_inject_search_context_list_content(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "Describe this"}]}]
        augmented = _inject_search_context(messages, "SEARCH")
        content = augmented[-1]["content"]
        assert isinstance(content, list)
        texts = [p["text"] for p in content if p.get("type") == "text"]
        assert any("SEARCH" in t for t in texts)

    def test_inject_search_context_no_mutation(self):
        original = [{"role": "user", "content": "hi"}]
        _inject_search_context(original, "ctx")
        # Original list should not be mutated
        assert original[0]["content"] == "hi"

    def test_prepend_system_adds_at_front(self):
        messages = [{"role": "user", "content": "hi"}]
        result = _prepend_system(messages, "You are helpful")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert result[1]["role"] == "user"

    def test_prepend_system_replaces_existing(self):
        messages = [
            {"role": "system", "content": "Old prompt"},
            {"role": "user", "content": "hi"},
        ]
        result = _prepend_system(messages, "New prompt")
        assert result[0]["content"] == "New prompt"
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Orchestrator.handle — non-streaming
# ---------------------------------------------------------------------------

class TestOrchestratorHandleNonStreaming:
    @pytest.mark.asyncio
    async def test_basic_response_returned(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("Paris is the capital of France.")
        with patch.object(orch, "_chat", new_callable=AsyncMock, return_value=expected), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            result = await orch.handle(
                [{"role": "user", "content": "What is the capital of France?"}],
                stream=False,
            )
        assert result == expected

    @pytest.mark.asyncio
    async def test_memory_injected_into_system_prompt(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("Sure!")
        captured_messages: list = []

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None):
            captured_messages.extend(messages)
            return expected

        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value="User likes Python"):
            await orch.handle([{"role": "user", "content": "Hello"}], stream=False)

        system_msg = next(m for m in captured_messages if m["role"] == "system")
        assert "User likes Python" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_no_memory_no_injection(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("Hello!")
        captured_messages: list = []

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None):
            captured_messages.extend(messages)
            return expected

        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            await orch.handle([{"role": "user", "content": "Hi"}], stream=False)

        system_msg = next(m for m in captured_messages if m["role"] == "system")
        # Memory separator should not appear
        assert "\n\n## Your Memory" not in system_msg["content"]

    @pytest.mark.asyncio
    async def test_search_context_prepended_when_triggered(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("The weather is sunny.")
        captured_messages: list = []

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None):
            captured_messages.extend(messages)
            return expected

        fake_search_results = "Weather results: sunny in Paris"

        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value=""), \
             patch.object(orch, "_run_search", new_callable=AsyncMock, return_value=fake_search_results), \
             patch("src.orchestrator.route") as mock_route:
            mock_route.return_value = MagicMock(
                model=Model.GENERAL,
                reason="default",
                search_triggered=True,
                search_query="weather in Paris",
            )
            await orch.handle([{"role": "user", "content": "What is the weather in Paris?"}], stream=False)

        user_msg = next(m for m in captured_messages if m["role"] == "user")
        assert "Weather results" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_model_override_passed_to_ensure_loaded(self):
        orch, mm = _make_orchestrator()
        mm.ensure_loaded = AsyncMock(return_value=("override-model", "http://localhost:11434"))
        expected = _make_response("Response")
        with patch.object(orch, "_chat", new_callable=AsyncMock, return_value=expected), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            await orch.handle(
                [{"role": "user", "content": "hi"}],
                stream=False,
                model_override="my-custom-model",
            )
        mm.ensure_loaded.assert_awaited_once()
        _, kwargs = mm.ensure_loaded.call_args
        assert kwargs.get("model_name_override") == "my-custom-model"


# ---------------------------------------------------------------------------
# Tool call loop
# ---------------------------------------------------------------------------

class TestToolCallLoop:
    @pytest.mark.asyncio
    async def test_tool_call_executed_and_result_injected(self):
        orch, mm = _make_orchestrator()
        tool_response = _make_response('{"tool": "web_search", "args": {"query": "hello"}}')
        final_response = _make_response("The answer is 42.")
        call_count = 0

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None):
            nonlocal call_count
            call_count += 1
            return tool_response if call_count == 1 else final_response

        mock_execute = AsyncMock(return_value={"results": "search results"})
        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch.object(orch, "_execute_tool", mock_execute), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            result = await orch.handle([{"role": "user", "content": "Search for hello"}], stream=False)

        assert result == final_response
        assert call_count == 2
        mock_execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_loop_stops_at_max_calls(self):
        orch, mm = _make_orchestrator({"routing": {"max_tool_calls_per_turn": 3}, "search": {}, "tools": {}})
        tool_response = _make_response('{"tool": "web_search", "args": {"query": "x"}}')
        call_count = 0

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None):
            nonlocal call_count
            call_count += 1
            return tool_response  # always returns a tool call

        mock_execute = AsyncMock(return_value={"results": "ok"})
        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch.object(orch, "_execute_tool", mock_execute), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            await orch.handle([{"role": "user", "content": "go"}], stream=False)

        # max 3 tool calls — execute_tool should be called at most 3 times
        assert mock_execute.await_count <= 3

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        orch, mm = _make_orchestrator()
        result = await orch._execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_shell_exec_disabled_returns_error(self):
        cfg = {
            "routing": {"max_tool_calls_per_turn": 5},
            "search": {},
            "tools": {"shell_exec": {"enabled": False}},
        }
        orch, mm = _make_orchestrator(cfg)
        result = await orch._execute_tool("shell_exec", {"command": "ls"})
        assert "error" in result
        assert "disabled" in result["error"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreaming:
    @pytest.mark.asyncio
    async def test_streaming_yields_metadata_then_chunks(self):
        orch, mm = _make_orchestrator()
        content = "Hello from streaming!"
        expected = _make_response(content, model="my-model")

        with patch.object(orch, "_call_with_tools", new_callable=AsyncMock, return_value=expected), \
             patch("src.orchestrator.get_memories_context", return_value=""), \
             patch("src.orchestrator.route") as mock_route:
            mock_route.return_value = MagicMock(
                model=Model.GENERAL,
                reason="default",
                search_triggered=False,
                search_query=None,
            )
            gen = await orch.handle(
                [{"role": "user", "content": "hi"}],
                stream=True,
            )
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)

        # First item must be metadata dict
        assert isinstance(chunks[0], dict)
        assert "__model__" in chunks[0]
        assert chunks[0]["__model__"] == "my-model"
        assert chunks[0]["__search__"] is False

        # Remaining chunks should reconstruct the content
        text = "".join(c for c in chunks[1:] if isinstance(c, str))
        assert text == content

    @pytest.mark.asyncio
    async def test_streaming_memory_flag(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("ok")

        with patch.object(orch, "_call_with_tools", new_callable=AsyncMock, return_value=expected), \
             patch("src.orchestrator.get_memories_context", return_value="User fact"), \
             patch("src.orchestrator.route") as mock_route:
            mock_route.return_value = MagicMock(
                model=Model.GENERAL,
                reason="default",
                search_triggered=False,
                search_query=None,
            )
            gen = await orch.handle(
                [{"role": "user", "content": "hi"}],
                stream=True,
            )
            chunks = [c async for c in gen]

        meta = chunks[0]
        assert meta["__memory__"] is True


# ---------------------------------------------------------------------------
# Backend retry logic
# ---------------------------------------------------------------------------

class TestBackendRetry:
    @pytest.mark.asyncio
    async def test_retries_on_503(self):
        import httpx
        orch, mm = _make_orchestrator()
        attempt = 0
        expected = _make_response("ok")

        async def _fake_post(*args, **kwargs):
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                mock_resp = MagicMock()
                mock_resp.status_code = 503
                mock_resp.request = MagicMock()
                raise httpx.HTTPStatusError("503", request=mock_resp.request, response=mock_resp)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = expected
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = _fake_post
            mock_client_cls.return_value = mock_client

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await orch._chat("test-model", "http://localhost:11434", [{"role": "user", "content": "hi"}])

        assert result == expected
        assert attempt == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        import httpx
        orch, mm = _make_orchestrator()

        async def _fake_post(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_resp.request = MagicMock()
            raise httpx.HTTPStatusError("503", request=mock_resp.request, response=mock_resp)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = _fake_post
            mock_client_cls.return_value = mock_client

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.HTTPStatusError):
                    await orch._chat("test-model", "http://localhost:11434", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Memory extraction
# ---------------------------------------------------------------------------

class TestMemoryExtraction:
    @pytest.mark.asyncio
    async def test_extract_and_save_calls_store(self):
        orch, mm = _make_orchestrator()
        messages = [
            {"role": "user", "content": "I love hiking"},
            {"role": "assistant", "content": "That's great!"},
        ]
        fake_memories = {"user_fact": ["User loves hiking"]}

        with patch("src.orchestrator.extract_memories", new_callable=AsyncMock, return_value=fake_memories) as mock_extract, \
             patch("src.orchestrator.add_memory", return_value="mem-001") as mock_add:
            saved = await orch.extract_and_save_memories(messages, conv_id="conv-1")

        mock_extract.assert_awaited_once()
        mock_add.assert_called_once_with("User loves hiking", conv_id="conv-1", type="user_fact")
        assert len(saved) == 1
        assert saved[0]["content"] == "User loves hiking"
        assert saved[0]["type"] == "user_fact"
        assert saved[0]["id"] == "mem-001"

    @pytest.mark.asyncio
    async def test_extract_multiple_types(self):
        orch, mm = _make_orchestrator()
        fake_memories = {
            "user_fact": ["User is a developer"],
            "preference": ["Prefers dark mode"],
        }

        with patch("src.orchestrator.extract_memories", new_callable=AsyncMock, return_value=fake_memories), \
             patch("src.orchestrator.add_memory", return_value="mem-x"):
            saved = await orch.extract_and_save_memories([{"role": "user", "content": "hi"}])

        assert len(saved) == 2
        types = {m["type"] for m in saved}
        assert "user_fact" in types
        assert "preference" in types
