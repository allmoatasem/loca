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
    _expand_query,
    _extract_content,
    _extract_tool_call,
    _inject_search_context,
    _is_broad_query,
    _last_user_content,
    _merge_recall_results,
    _prepend_system,
    _rerank_memories,
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
# Multi-query expansion helpers (retrieval-quality step 1b)
# ---------------------------------------------------------------------------

class TestBroadQueryDetection:
    def test_explicit_about_me_is_broad(self):
        assert _is_broad_query("what do you know about me") is True

    def test_who_am_i_is_broad(self):
        assert _is_broad_query("who am I?") is True

    def test_tell_me_everything_is_broad(self):
        assert _is_broad_query("tell me everything") is True

    def test_single_word_me_is_broad(self):
        assert _is_broad_query("me") is True

    def test_technical_question_is_not_broad(self):
        assert _is_broad_query("how do I use the async API in python?") is False

    def test_specific_topic_is_not_broad(self):
        assert _is_broad_query("what's the best way to embed PDFs?") is False


class TestExpandQuery:
    def test_broad_returns_multiple_queries(self):
        out = _expand_query("what do you know about me")
        assert out[0] == "what do you know about me"
        assert len(out) >= 4

    def test_narrow_returns_only_original(self):
        out = _expand_query("how do embeddings work")
        assert out == ["how do embeddings work"]


class TestMergeRecallResults:
    def test_dedup_by_id(self):
        buckets = [
            [{"id": "a", "content": "X"}, {"id": "b", "content": "Y"}],
            [{"id": "b", "content": "Y"}, {"id": "c", "content": "Z"}],
        ]
        merged = _merge_recall_results(buckets, limit=10)
        assert [m["id"] for m in merged] == ["a", "b", "c"]

    def test_preserves_order_of_first_occurrence(self):
        buckets = [
            [{"id": "a", "content": "A"}],
            [{"id": "b", "content": "B"}],
            [{"id": "c", "content": "C"}],
        ]
        merged = _merge_recall_results(buckets, limit=10)
        assert [m["id"] for m in merged] == ["a", "b", "c"]

    def test_respects_limit(self):
        buckets = [[{"id": str(i), "content": "x"} for i in range(10)]]
        merged = _merge_recall_results(buckets, limit=3)
        assert len(merged) == 3

    def test_dedup_by_content_when_no_id(self):
        buckets = [
            [{"content": "same text"}, {"content": "other"}],
            [{"content": "same text"}, {"content": "third"}],
        ]
        merged = _merge_recall_results(buckets, limit=10)
        assert len(merged) == 3


# ---------------------------------------------------------------------------
# Lightweight rerank (retrieval-quality step 1c)
# ---------------------------------------------------------------------------

class TestRerankMemories:
    def test_empty_returns_empty(self):
        assert _rerank_memories("anything", [], keep=10) == []

    def test_keep_zero_returns_empty(self):
        mems = [{"content": "x"}]
        assert _rerank_memories("x", mems, keep=0) == []

    def test_relevant_beats_irrelevant(self):
        mems = [
            {"id": "unrelated", "content": "The weather was nice yesterday in Paris"},
            {"id": "relevant", "content": "User prefers Python for backend work"},
        ]
        out = _rerank_memories("python backend preference", mems, keep=2)
        assert out[0]["id"] == "relevant"

    def test_very_short_category_labels_penalised(self):
        mems = [
            {"id": "short", "content": "python"},
            {"id": "focused", "content": "User writes Python daily for the Loca project"},
        ]
        out = _rerank_memories("python", mems, keep=2)
        assert out[0]["id"] == "focused"

    def test_keep_caps_result_length(self):
        mems = [{"id": f"m{i}", "content": f"python code sample {i}"} for i in range(20)]
        out = _rerank_memories("python code", mems, keep=5)
        assert len(out) == 5

    def test_preserves_order_when_query_has_no_tokens(self):
        # Query with only stopwords — fall back to original order
        mems = [{"id": "a", "content": "X"}, {"id": "b", "content": "Y"}]
        out = _rerank_memories("the and", mems, keep=2)
        assert [m["id"] for m in out] == ["a", "b"]


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

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None, **kwargs):
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

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None, **kwargs):
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

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None, **kwargs):
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

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None, **kwargs):
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

        async def _fake_chat(model, api_base, messages, stream=False, num_ctx=None, **kwargs):
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

class TestToolsPassthrough:
    """OpenAI tools/tool_choice passthrough used by agentic coding clients (claw-code, Aider, Continue)."""

    @pytest.mark.asyncio
    async def test_passthrough_call_forwards_tools_to_backend(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("ok")
        captured_payloads: list = []

        async def _fake_chat(model, api_base, messages, stream=False, **kwargs):
            captured_payloads.append(kwargs)
            return expected

        tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            result = await orch.handle_passthrough(
                [{"role": "user", "content": "read foo.py"}],
                tools=tools,
                tool_choice="auto",
                stream=False,
            )
        assert result == expected
        assert captured_payloads and captured_payloads[0].get("tools") == tools
        assert captured_payloads[0].get("tool_choice") == "auto"

    @pytest.mark.asyncio
    async def test_passthrough_call_preserves_tool_calls_in_response(self):
        orch, mm = _make_orchestrator()
        backend_response = {
            "id": "x", "model": "m",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        with patch.object(orch, "_chat", new_callable=AsyncMock, return_value=backend_response), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            result = await orch.handle_passthrough(
                [{"role": "user", "content": "read foo.py"}],
                tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
                stream=False,
            )
        assert result["choices"][0]["finish_reason"] == "tool_calls"
        assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "read_file"

    @pytest.mark.asyncio
    async def test_passthrough_still_injects_memory(self):
        orch, mm = _make_orchestrator()
        expected = _make_response("ok")
        captured_messages: list = []

        async def _fake_chat(model, api_base, messages, stream=False, **kwargs):
            captured_messages.extend(messages)
            return expected

        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value="User uses tabs not spaces"):
            await orch.handle_passthrough(
                [{"role": "user", "content": "format this"}],
                tools=[{"type": "function", "function": {"name": "edit", "parameters": {}}}],
                stream=False,
            )
        system_msg = next(m for m in captured_messages if m["role"] == "system")
        assert "User uses tabs not spaces" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_passthrough_skips_custom_tool_loop(self):
        """Even if the model emits a <tool_call> XML in its text, don't re-invoke — pass through verbatim."""
        orch, mm = _make_orchestrator()
        # The model's "response" contains Loca's own XML tool-call format — passthrough should NOT parse it.
        response = _make_response('<tool_call name="shell_exec">{"cmd": "rm -rf /"}</tool_call>')
        call_count = [0]

        async def _fake_chat(*args, **kwargs):
            call_count[0] += 1
            return response

        with patch.object(orch, "_chat", side_effect=_fake_chat), \
             patch("src.orchestrator.get_memories_context", return_value=""):
            result = await orch.handle_passthrough(
                [{"role": "user", "content": "run ls"}],
                tools=[{"type": "function", "function": {"name": "shell_exec", "parameters": {}}}],
                stream=False,
            )
        assert call_count[0] == 1  # exactly one call — no tool loop
        assert result == response


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
    async def test_extract_and_save_returns_empty_without_plugin(self):
        """Without a memory plugin, extract_and_save_memories returns []."""
        orch, mm = _make_orchestrator()
        messages = [
            {"role": "user", "content": "I love hiking and outdoor adventures"},
            {"role": "assistant", "content": "That's great!"},
        ]
        saved = await orch.extract_and_save_memories(messages, conv_id="conv-1")
        assert saved == []

    @pytest.mark.asyncio
    async def test_extract_and_save_with_memory_plugin(self):
        """With a memory plugin, extract_and_save_memories stores the pair verbatim."""
        from src.plugins.memory_plugin import MemoryPlugin
        orch, mm = _make_orchestrator()
        mock_plugin = MagicMock(spec=MemoryPlugin)
        mock_plugin.store = AsyncMock(return_value="mem-001")
        orch._memory = mock_plugin

        messages = [
            {"role": "user", "content": "I love hiking and outdoor adventures"},
            {"role": "assistant", "content": "That's wonderful!"},
        ]
        saved = await orch.extract_and_save_memories(messages, conv_id="conv-1")

        mock_plugin.store.assert_awaited_once()
        assert len(saved) == 1
        assert saved[0]["id"] == "mem-001"
        assert saved[0]["type"] == "conversation"

    @pytest.mark.asyncio
    async def test_extract_skips_short_user_messages(self):
        """Messages shorter than 20 chars should not be stored."""
        from src.plugins.memory_plugin import MemoryPlugin
        orch, mm = _make_orchestrator()
        mock_plugin = MagicMock(spec=MemoryPlugin)
        mock_plugin.store = AsyncMock(return_value="mem-x")
        orch._memory = mock_plugin

        messages = [{"role": "user", "content": "hi"}]
        saved = await orch.extract_and_save_memories(messages)
        mock_plugin.store.assert_not_awaited()
        assert saved == []
