"""Unit tests for `src.watches_runner.run_watch_once`.

The runner is the only interesting piece — the scheduler loop is a
thin wrapper that calls `run_watch_once` on each due row. Test the
executor in isolation with:
- fresh watch + no hits → no items, hash updates
- fresh watch + real hits → items appended, hash records URLs
- repeat run with same URLs → no-op (unchanged flag, no new items)
- new URL appears → only the delta gets appended
- timeout → asyncio.TimeoutError propagates
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.store as store_module  # noqa: E402
from src.tools.web_search import SearchResult  # noqa: E402
from src.watches_runner import run_watch_once  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Fresh SQLite per test — same pattern test_store uses."""
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        yield db_path


def _make_project_with_watch(sub_scope: str = "new arxiv papers on al-andalus") -> dict:
    """Create a project + one watch. Returns the watch row for passing
    into `run_watch_once`."""
    pid = store_module.create_project("Test", scope="Test scope")
    store_module.create_project_watch(pid, sub_scope, 60)
    rows = store_module.list_project_watches(pid)
    assert rows, "watch creation should yield a row"
    return rows[0]


async def _search_stub(results: list[SearchResult]):
    """Build an awaitable that ignores args and returns the given hits."""
    async def _fn(**_kwargs):
        return results
    return _fn


@pytest.mark.asyncio
async def test_empty_hits_marks_ran_and_no_items():
    watch = _make_project_with_watch()
    fn = await _search_stub([])
    result = await run_watch_once(
        watch, searxng_url="http://unused", web_search_fn=fn,
    )
    assert result.total_hits == 0
    assert result.new_count == 0
    # With no prior hash, empty snapshot still counts as a state update —
    # but our impl treats the first-ever run with any result set
    # (including empty) as changed, so we record the hash either way.
    assert result.snapshot_hash  # non-empty sha256
    # No project_items written.
    items = store_module.list_project_items(watch["project_id"], kind="web_url")
    assert items == []
    # last_run should be set.
    after = store_module.list_project_watches(watch["project_id"])[0]
    assert after["last_run"] is not None


@pytest.mark.asyncio
async def test_fresh_hits_appended_as_web_url_items():
    watch = _make_project_with_watch()
    hits = [
        SearchResult(url="https://example.com/a", title="A", snippet="first", content=""),
        SearchResult(url="https://example.com/b", title="B", snippet="second", content=""),
    ]
    fn = await _search_stub(hits)
    result = await run_watch_once(
        watch, searxng_url="http://unused", web_search_fn=fn,
    )
    assert result.total_hits == 2
    assert result.new_count == 2
    assert result.unchanged is False
    items = store_module.list_project_items(watch["project_id"], kind="web_url")
    urls = {it["url"] for it in items}
    assert urls == {"https://example.com/a", "https://example.com/b"}


@pytest.mark.asyncio
async def test_repeat_run_same_urls_is_unchanged_noop():
    watch = _make_project_with_watch()
    hits = [
        SearchResult(url="https://example.com/a", title="A", snippet="x", content=""),
    ]
    # First run: stores one URL + hash.
    fn = await _search_stub(hits)
    first = await run_watch_once(watch, searxng_url="http://unused", web_search_fn=fn)
    assert first.new_count == 1

    # Re-fetch the watch to pick up the persisted last_snapshot_hash.
    fresh = store_module.list_project_watches(watch["project_id"])[0]
    second = await run_watch_once(fresh, searxng_url="http://unused", web_search_fn=fn)
    assert second.unchanged is True
    assert second.new_count == 0
    # Still only the one item.
    items = store_module.list_project_items(watch["project_id"], kind="web_url")
    assert len(items) == 1


@pytest.mark.asyncio
async def test_delta_only_new_urls_appended():
    watch = _make_project_with_watch()
    first_hits = [
        SearchResult(url="https://example.com/a", title="A", snippet="x", content=""),
    ]
    fn1 = await _search_stub(first_hits)
    await run_watch_once(watch, searxng_url="http://unused", web_search_fn=fn1)

    fresh = store_module.list_project_watches(watch["project_id"])[0]
    second_hits = [
        # /a repeats — should be skipped
        SearchResult(url="https://example.com/a", title="A", snippet="x", content=""),
        # /b is new — should be added
        SearchResult(url="https://example.com/b", title="B", snippet="y", content=""),
    ]
    fn2 = await _search_stub(second_hits)
    result = await run_watch_once(fresh, searxng_url="http://unused", web_search_fn=fn2)
    assert result.unchanged is False
    assert result.new_count == 1, "only /b is new; /a already exists"
    items = store_module.list_project_items(watch["project_id"], kind="web_url")
    urls = {it["url"] for it in items}
    assert urls == {"https://example.com/a", "https://example.com/b"}


@pytest.mark.asyncio
async def test_timeout_propagates():
    watch = _make_project_with_watch()

    async def slow_search(**_kwargs):
        await asyncio.sleep(5)
        return []

    with pytest.raises(asyncio.TimeoutError):
        await run_watch_once(
            watch,
            searxng_url="http://unused",
            web_search_fn=slow_search,
            timeout_s=0.1,
        )
    # Timeout should NOT mark the watch as ran — we never got to
    # mark_watch_ran inside the timeout block.
    after = store_module.list_project_watches(watch["project_id"])[0]
    assert after["last_run"] is None
