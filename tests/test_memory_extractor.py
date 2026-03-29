"""
Tests for memory_extractor — three-pass extraction.

Run with: pytest tests/test_memory_extractor.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.memory_extractor import (
    extract_memories,
    _has_tool_results,
    _has_user_correction,
    _format_conversation,
)


def mock_llm_response(content: str):
    """Build a minimal OpenAI-shaped response dict."""
    return {
        "choices": [{"message": {"content": content}}]
    }


# ── Helper tests ───────────────────────────────────────────────────────────────

def test_has_tool_results_true():
    msgs = [
        {"role": "assistant", "content": '<tool_result tool="web_search">[results]</tool_result>'},
    ]
    assert _has_tool_results(msgs) is True


def test_has_tool_results_false():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    assert _has_tool_results(msgs) is False


def test_has_user_correction_true():
    msgs = [{"role": "user", "content": "No, you were wrong about that. Learn from this."}]
    assert _has_user_correction(msgs) is True


def test_has_user_correction_false():
    msgs = [{"role": "user", "content": "Thanks, that was helpful!"}]
    assert _has_user_correction(msgs) is False


def test_format_conversation_filters_roles():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    result = _format_conversation(msgs)
    assert "USER: Hello" in result
    assert "ASSISTANT: Hi" in result
    assert "SYSTEM" not in result


# ── extract_memories ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extracts_user_facts():
    msgs = [
        {"role": "user", "content": "I prefer Python and I'm building an app called Loca on macOS"},
        {"role": "assistant", "content": "Noted!"},
    ]

    user_fact_response = '["Prefers Python", "Building app called Loca on macOS"]'
    empty_response = "[]"

    call_count = 0
    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value=mock_llm_response(user_fact_response))
        return m

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    assert "Prefers Python" in result["user_fact"]
    assert "Building app called Loca on macOS" in result["user_fact"]


@pytest.mark.asyncio
async def test_skips_knowledge_pass_without_tool_results():
    msgs = [
        {"role": "user", "content": "What's the capital of France?"},
        {"role": "assistant", "content": "Paris."},
    ]

    call_counter = {"n": 0}

    async def fake_post(url, **kwargs):
        call_counter["n"] += 1
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value=mock_llm_response("[]"))
        return m

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    # Only 1 pass (user_fact), knowledge and correction skipped
    assert call_counter["n"] == 1
    assert result["knowledge"] == []


@pytest.mark.asyncio
async def test_extracts_knowledge_from_tool_results():
    msgs = [
        {"role": "user", "content": "Who won the 2026 Oscars?"},
        {"role": "assistant", "content": '<tool_result tool="web_search">Best Picture: One Battle After Another</tool_result>\nThe 2026 Best Picture was One Battle After Another.'},
    ]

    responses = iter([
        "[]",  # user_fact pass
        '["2026 Oscar Best Picture: One Battle After Another — source: web_search"]',  # knowledge pass
    ])

    async def fake_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value=mock_llm_response(next(responses)))
        return m

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    assert len(result["knowledge"]) == 1
    assert "One Battle After Another" in result["knowledge"][0]


@pytest.mark.asyncio
async def test_extracts_correction():
    msgs = [
        {"role": "assistant", "content": "I cannot verify this as it's beyond my training."},
        {"role": "user", "content": "No, you searched and found the answer. You should trust your search results."},
    ]

    responses = iter([
        "[]",  # user_fact
        # knowledge pass skipped (no tool_result tags)
        '["Trust web_search results as ground truth even if they contradict training data cutoff"]',  # correction
    ])

    async def fake_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value=mock_llm_response(next(responses)))
        return m

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    assert len(result["correction"]) == 1
    assert "Trust" in result["correction"][0]


@pytest.mark.asyncio
async def test_returns_empty_on_small_talk():
    msgs = [
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm doing well!"},
    ]

    async def fake_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value=mock_llm_response("[]"))
        return m

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    assert result["user_fact"] == []
    assert result["knowledge"] == []
    assert result["correction"] == []


@pytest.mark.asyncio
async def test_handles_extractor_http_error_gracefully():
    msgs = [{"role": "user", "content": "I love Rust"}, {"role": "assistant", "content": "Nice!"}]
    import httpx

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        mock_cls.return_value = mock_client

        result = await extract_memories(msgs, "test-model", "http://localhost:18080")

    # Should return empty rather than raise
    assert result["user_fact"] == []
