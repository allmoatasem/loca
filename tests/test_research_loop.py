"""Unit tests for `src.research_loop.run_research_loop`.

The loop has three roles so we're testing:
- Researcher plans sub-queries and gathers sources
- Writer synthesises with citations into a single answer
- Verifier flags phantom [memory: N] citations

Each test injects fake `chat_fn` and `search_fn` so we never hit the
live backend or the network.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.research_loop import (  # noqa: E402
    LoopSource,
    _parse_sub_queries,
    run_research_loop,
)
from src.tools.web_search import SearchResult  # noqa: E402


def _make_chat_fn(responses: list[str]):
    """Return an async fn that hands back `responses` in order. Raises
    if the loop asks for more calls than we pre-wrote — keeps the test
    honest about how many LLM calls the loop should make."""
    queue = list(responses)

    async def _fn(*, messages: list[dict], **kwargs):
        assert queue, f"chat_fn called more times than expected ({len(responses)})"
        content = queue.pop(0)
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return _fn


def _make_search_fn(results_per_query: dict[str, list[SearchResult]] | list[SearchResult]):
    """If passed a dict, routes by query string. If a list, returns the
    same hits regardless of query (convenient default)."""

    async def _fn(*, query: str, max_results: int = 5):
        if isinstance(results_per_query, dict):
            return results_per_query.get(query, [])
        return results_per_query
    return _fn


async def _collect(gen):
    """Drain an async generator into a single joined string."""
    out = []
    async for piece in gen:
        out.append(piece)
    return "".join(out)


# ---------------------------------------------------------------------
# sub-query parser (pure function)
# ---------------------------------------------------------------------

def test_parse_sub_queries_accepts_clean_json():
    qs = _parse_sub_queries('["foo", "bar", "baz"]', fallback="x", n=3)
    assert qs == ["foo", "bar", "baz"]


def test_parse_sub_queries_strips_code_fences():
    text = '```json\n["a", "b"]\n```'
    qs = _parse_sub_queries(text, fallback="x", n=3)
    assert qs == ["a", "b"]


def test_parse_sub_queries_falls_back_to_bullets_then_single():
    # Model returned bullets instead of JSON.
    text = "- query one\n- query two\n- query three"
    qs = _parse_sub_queries(text, fallback="fallback", n=3)
    # Bullet fallback is best-effort — we just need something non-empty.
    assert qs  # not empty
    assert all(isinstance(q, str) and q for q in qs)


def test_parse_sub_queries_returns_fallback_when_unusable():
    qs = _parse_sub_queries("", fallback="the fallback", n=3)
    assert qs == ["the fallback"]


# ---------------------------------------------------------------------
# End-to-end loop
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_gathers_sources_and_writes_answer(tmp_path, monkeypatch):
    # Isolate the plan-checkpoint directory so we don't touch the user's
    # Application Support dir during tests.
    monkeypatch.setenv("LOCA_DATA_DIR", str(tmp_path))

    researcher_plan = json.dumps(["umayyad conquest", "abd al-rahman cordoba"])
    writer_answer = (
        "The Muslim conquest of Iberia began in 711 CE [memory: 2]. "
        "Abd al-Rahman I later founded Cordoba as an emirate [memory: 3]."
    )
    chat_fn = _make_chat_fn([researcher_plan, writer_answer])

    search_fn = _make_search_fn([
        SearchResult(
            url="https://example.com/conquest",
            title="Umayyad conquest of Hispania",
            snippet="Tariq ibn Ziyad crossed the strait in 711 CE.",
            content="",
        ),
        SearchResult(
            url="https://example.com/cordoba",
            title="Abd al-Rahman I",
            snippet="Founded the Emirate of Cordoba in 756.",
            content="",
        ),
    ])

    memory = [
        LoopSource(idx=1, origin="memory", title="Prior note",
                   snippet="User reads about Al-Andalus frequently."),
    ]

    output = await _collect(run_research_loop(
        chat_fn=chat_fn,
        search_fn=search_fn,
        user_query="How did Muslim rule in Spain start?",
        history=[],
        memory_sources=memory,
        conv_id="conv-test-happy",
    ))

    assert "<think>" in output
    assert "Umayyad conquest of Hispania" in output or "umayyad conquest" in output.lower()
    assert "711 CE" in output
    # Plan file got written.
    plan_path = tmp_path / "plans" / "conv-test-happy.md"
    assert plan_path.exists()
    content = plan_path.read_text()
    assert "**Phase:** done" in content
    assert "Sub-queries planned" in content


@pytest.mark.asyncio
async def test_phantom_citation_flagged_in_footer(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCA_DATA_DIR", str(tmp_path))

    # Writer invents [memory: 99] — outside the tiny pool.
    chat_fn = _make_chat_fn([
        json.dumps(["query-one"]),
        "According to [memory: 99] the sky is green.",
    ])
    search_fn = _make_search_fn([])  # no web hits to keep pool small
    memory = [
        LoopSource(idx=1, origin="memory", title="a", snippet="b"),
    ]

    output = await _collect(run_research_loop(
        chat_fn=chat_fn, search_fn=search_fn,
        user_query="Is the sky green?",
        history=[],
        memory_sources=memory,
        conv_id="conv-test-phantom",
    ))
    assert "Citation check" in output
    assert "99" in output

    plan_path = tmp_path / "plans" / "conv-test-phantom.md"
    assert "**Phantom citations:** [99]" in plan_path.read_text()


@pytest.mark.asyncio
async def test_researcher_failure_surfaces_clean_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCA_DATA_DIR", str(tmp_path))

    async def broken_chat_fn(**kwargs):
        raise RuntimeError("backend exploded")

    output = await _collect(run_research_loop(
        chat_fn=broken_chat_fn,
        search_fn=_make_search_fn([]),
        user_query="unused",
        history=[],
        memory_sources=[],
        conv_id="conv-test-error",
    ))
    assert "Research loop failed" in output
    assert "backend exploded" in output

    plan_path = tmp_path / "plans" / "conv-test-error.md"
    content = plan_path.read_text()
    assert "**Phase:** error" in content
    assert "backend exploded" in content


@pytest.mark.asyncio
async def test_search_failure_is_non_fatal(tmp_path, monkeypatch):
    """If web_search raises, the loop should keep going with whatever it
    has — in this case the memory pool plus no web hits."""
    monkeypatch.setenv("LOCA_DATA_DIR", str(tmp_path))

    chat_fn = _make_chat_fn([
        json.dumps(["a query"]),
        "Based on what I know [memory: 1], the answer is X.",
    ])

    async def broken_search_fn(**kwargs):
        raise RuntimeError("searxng down")

    memory = [LoopSource(idx=1, origin="memory", title="x", snippet="y")]
    output = await _collect(run_research_loop(
        chat_fn=chat_fn,
        search_fn=broken_search_fn,
        user_query="anything",
        history=[],
        memory_sources=memory,
        conv_id="conv-test-search-down",
    ))
    # Answer still surfaces.
    assert "[memory: 1]" in output
    # Progress block mentions the failure so the user sees why.
    assert "web search failed" in output
