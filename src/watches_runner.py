"""Background runner for Research Partner watches.

PR #90 shipped the `project_watches` schema + UI: users can create a
watch on a project, pick a cadence, and see it listed. This module is
what actually makes them fire. A single-shot agent per watch cycle:
search the sub-scope → diff top URLs against last snapshot → append
new URLs as `web_url` project_items.

The scheduler lives in `src.proxy.lifespan` which owns the asyncio
task lifecycle. This module stays dependency-free so the executor can
be unit-tested without spinning up a FastAPI app — we inject the
`web_search` callable + the store helpers rather than importing them
at function scope.

Deliberately simple for v1:
- No multi-subagent decomposition (researcher/reviewer/writer/verifier
  is the follow-up PR).
- No page-content ingestion — just title + snippet + URL. Heavy fetch
  belongs to the importer path and is out of scope.
- No notification surface. The user finds new hits by opening Sources.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .store import (
    add_project_item,
    list_due_watches,
    list_project_items,
    mark_watch_ran,
)
from .tools.web_search import SearchResult, web_search

logger = logging.getLogger(__name__)

# Shorter than PR #90's 5-minute tick. With schedule_minutes-gated
# dispatch inside list_due_watches, a faster tick just improves the
# lag between "watch becomes due" and "watch runs" — it doesn't cause
# extra searches. Two minutes is a good balance between responsiveness
# and idle-CPU noise.
DEFAULT_TICK_SECONDS = 120
DEFAULT_PER_WATCH_TIMEOUT = 90.0
DEFAULT_MAX_RESULTS = 5

# Signature of the injectable search function — keeps the unit tests
# from having to monkey-patch a module-level import.
SearchFn = Callable[..., Awaitable[list[SearchResult]]]


@dataclass
class WatchRunResult:
    watch_id: str
    project_id: str
    total_hits: int
    new_count: int
    unchanged: bool
    snapshot_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "project_id": self.project_id,
            "total_hits": self.total_hits,
            "new_count": self.new_count,
            "unchanged": self.unchanged,
            "snapshot_hash": self.snapshot_hash,
        }


async def run_watch_once(
    watch: dict,
    *,
    searxng_url: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout_s: float = DEFAULT_PER_WATCH_TIMEOUT,
    web_search_fn: SearchFn | None = None,
) -> WatchRunResult:
    """Execute one watch cycle.

    Behaviour:
    - Runs `web_search(sub_scope)`, takes the top `max_results` URLs.
    - Computes a sha256 of the sorted URL list. If it matches the watch's
      previous `last_snapshot_hash`, no-op (still bumps `last_run`).
    - Otherwise, appends URLs that aren't already `web_url` project_items
      for this project (dedup by `add_project_item`'s content_hash).
    - Updates `last_run` + `last_snapshot_hash` regardless of outcome.

    Timeouts are a hard wall around the whole cycle — if search hangs or
    append stalls, the watch is abandoned for this tick with an
    `asyncio.TimeoutError`. The surrounding loop logs + moves on.
    """
    search = web_search_fn or web_search
    async with asyncio.timeout(timeout_s):
        hits = await search(
            query=watch["sub_scope"],
            searxng_url=searxng_url,
            max_results=max_results,
            research_mode=False,
        )
        urls = [h.url for h in hits if h.url]
        snapshot_hash = _hash_url_list(urls)

        if snapshot_hash == (watch.get("last_snapshot_hash") or ""):
            mark_watch_ran(watch["id"], snapshot_hash)
            return WatchRunResult(
                watch_id=watch["id"],
                project_id=watch["project_id"],
                total_hits=len(hits),
                new_count=0,
                unchanged=True,
                snapshot_hash=snapshot_hash,
            )

        prev_urls = _existing_urls_for_project(watch["project_id"])
        new_count = 0
        for hit in hits:
            if not hit.url or hit.url in prev_urls:
                continue
            content_hash = hashlib.sha256(f"url:{hit.url}".encode()).hexdigest()
            iid = add_project_item(
                watch["project_id"],
                kind="web_url",
                title=(hit.title or hit.url).strip(),
                body=(hit.snippet or "")[:500],
                url=hit.url,
                content_hash=content_hash,
            )
            if iid is not None:
                new_count += 1

        mark_watch_ran(watch["id"], snapshot_hash)
        return WatchRunResult(
            watch_id=watch["id"],
            project_id=watch["project_id"],
            total_hits=len(hits),
            new_count=new_count,
            unchanged=False,
            snapshot_hash=snapshot_hash,
        )


async def watches_loop(
    *,
    tick_seconds: int = DEFAULT_TICK_SECONDS,
    per_watch_timeout_s: float = DEFAULT_PER_WATCH_TIMEOUT,
    max_results: int = DEFAULT_MAX_RESULTS,
    searxng_url: str | None = None,
) -> None:
    """The long-running scheduler. Intended to be wrapped in
    `asyncio.create_task` by the FastAPI lifespan. Cancellation is
    handled cleanly — the caller should `task.cancel()` on shutdown.
    """
    resolved_searxng = searxng_url or os.environ.get(
        "SEARXNG_URL", "http://localhost:8888",
    )
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            due = list_due_watches()
            if not due:
                continue
            logger.info("watches tick: %d due", len(due))
            executed = 0
            for watch in due:
                try:
                    result = await run_watch_once(
                        watch,
                        searxng_url=resolved_searxng,
                        max_results=max_results,
                        timeout_s=per_watch_timeout_s,
                    )
                    executed += 1
                    if result.new_count:
                        logger.info(
                            "watch %s appended %d new URLs to project %s",
                            result.watch_id, result.new_count, result.project_id,
                        )
                except asyncio.TimeoutError:
                    logger.warning(
                        "watch %s timed out after %ss — skipped this tick",
                        watch["id"], per_watch_timeout_s,
                    )
                except Exception as exc:
                    # One bad watch shouldn't kill the rest of the tick.
                    logger.warning("watch %s failed: %s", watch["id"], exc)
            if executed:
                logger.info("watches tick: executed=%d", executed)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover
            # Whole-tick failure — log and keep the loop alive so a
            # transient SQLite/config hiccup doesn't kill the scheduler.
            logger.warning("watches loop iteration failed: %s", exc)


# ---------------------------------------------------------------------
# Helpers (module-private; exposed only for readability in tests)
# ---------------------------------------------------------------------

def _hash_url_list(urls: list[str]) -> str:
    """Stable snapshot hash over the (sorted) URL list. Sorting means
    the hash is insensitive to SearXNG ranking jitter — we only care
    whether the *set* of top results changed."""
    joined = "\n".join(sorted(urls))
    return hashlib.sha256(joined.encode()).hexdigest()


def _existing_urls_for_project(project_id: str) -> set[str]:
    """Prior `web_url` items for this project, keyed by URL. Belt-and-
    braces against content_hash collisions and against older rows that
    pre-date the hashing convention."""
    return {
        row["url"]
        for row in list_project_items(project_id, kind="web_url", limit=500)
        if row.get("url")
    }
