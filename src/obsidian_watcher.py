"""Obsidian Watcher — app-level background vault sync.

Replaces the per-project `Sync Vault` flow with a single watched-vault
registry that the server rescans on a cadence. Downstream retrieval
(Research Partner + Vault Analyser) reads straight from the shared
`vault_notes` index, so adding a vault as a project source is free:
no re-ingestion, no duplicated state.

The loop:
- Ticks every `DEFAULT_TICK_SECONDS`.
- For each enabled watched vault whose `last_scan_at` is older than its
  `scan_interval_s`, runs `vault_indexer.scan_vault(path)` off the event
  loop via `asyncio.to_thread` so other API requests (model loading,
  chat streaming) stay responsive.
- Persists the returned stats back onto the row so the UI can display
  "last synced 3m ago — 412 notes".

The module is intentionally dependency-light so it's unit-testable
without a FastAPI app: `scan_now` + `watcher_loop` both take an
injectable scanner callable for tests.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from . import store
from .vault_indexer import scan_vault, validate_vault_path

logger = logging.getLogger(__name__)

# How often the loop wakes. With per-vault schedule_interval_s gating,
# a faster tick only lowers the lag between "vault due" and "vault
# rescanned". 60s is a good balance between responsiveness and idle
# CPU; the actual scan cost is dominated by changed-file parsing, and
# unchanged notes hit the content_hash fast-path in scan_vault().
DEFAULT_TICK_SECONDS = 60
DEFAULT_SCAN_TIMEOUT_S = 120.0

# Per-vault re-entrancy guard. scan_now() and the loop both acquire
# the same lock, so an in-flight scan never races with a user-
# triggered scan-now, and two ticks can't both fire on the same vault.
_SCAN_LOCKS: dict[str, asyncio.Lock] = {}
_SCAN_BUSY: set[str] = set()

ScanFn = Callable[[str], dict]


def _lock_for(path: str) -> asyncio.Lock:
    lock = _SCAN_LOCKS.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _SCAN_LOCKS[path] = lock
    return lock


def is_busy(path: str) -> bool:
    return path in _SCAN_BUSY


def busy_paths() -> list[str]:
    return sorted(_SCAN_BUSY)


def register(path: str, *, scan_interval_s: int = 300) -> dict:
    """Register a vault for background sync. Validates + upserts + returns
    the stored row. Raises ValueError on an invalid vault path so the
    caller can turn it into a 400."""
    resolved = str(Path(path).expanduser().resolve())
    err = validate_vault_path(resolved)
    if err:
        raise ValueError(err)
    name = Path(resolved).name or resolved
    store.upsert_watched_vault(
        resolved, name=name, scan_interval_s=scan_interval_s,
    )
    row = store.get_watched_vault(resolved)
    assert row is not None, "upsert did not create row"
    return row


def unregister(path: str) -> None:
    """Drop a vault from the registry. Leaves `vault_notes` in place so
    already-indexed data stays searchable until the user explicitly
    clears it."""
    resolved = str(Path(path).expanduser().resolve())
    store.delete_watched_vault(resolved)


def set_enabled(path: str, enabled: bool) -> None:
    resolved = str(Path(path).expanduser().resolve())
    store.set_watched_vault_enabled(resolved, enabled)


def list_watched() -> list[dict]:
    """Registered vaults + live busy flag so the UI can show spinners."""
    rows = store.list_watched_vaults()
    for r in rows:
        r["busy"] = r["path"] in _SCAN_BUSY
    return rows


async def scan_now(
    path: str, *,
    scan_fn: ScanFn | None = None,
    timeout_s: float = DEFAULT_SCAN_TIMEOUT_S,
) -> dict:
    """Run a scan immediately, respecting the per-vault lock. Returns
    the scanner's stats dict merged with the path."""
    resolved = str(Path(path).expanduser().resolve())
    scanner = scan_fn or scan_vault
    lock = _lock_for(resolved)
    async with lock:
        _SCAN_BUSY.add(resolved)
        try:
            async with asyncio.timeout(timeout_s):
                stats = await asyncio.to_thread(scanner, resolved)
            store.mark_watched_vault_scanned(resolved, stats)
            # Invalidate the TF-IDF cache so the next semantic query
            # picks up newly indexed notes without a manual refresh.
            try:
                from .vault_search import clear_vault_search_cache  # noqa: PLC0415
                clear_vault_search_cache(resolved)
            except Exception:
                pass
            return {"path": resolved, **stats}
        finally:
            _SCAN_BUSY.discard(resolved)


def _is_due(vault: dict, now: float) -> bool:
    if not vault.get("enabled"):
        return False
    last = vault.get("last_scan_at") or 0.0
    interval = int(vault.get("scan_interval_s") or 300)
    return (now - last) >= interval


async def _tick_once(
    *, scan_fn: ScanFn | None = None, timeout_s: float = DEFAULT_SCAN_TIMEOUT_S,
) -> int:
    """One pass over the registry. Returns the number of vaults
    scanned this tick. Extracted so the test suite can pump the loop
    without managing sleep."""
    import time as _time  # noqa: PLC0415

    now = _time.time()
    scanned = 0
    for vault in store.list_watched_vaults():
        if not _is_due(vault, now):
            continue
        if vault["path"] in _SCAN_BUSY:
            continue
        try:
            await scan_now(vault["path"], scan_fn=scan_fn, timeout_s=timeout_s)
            scanned += 1
        except asyncio.TimeoutError:
            logger.warning(
                "obsidian-watcher: scan of %s timed out after %ss",
                vault["path"], timeout_s,
            )
        except Exception as exc:
            # One bad vault shouldn't kill the rest of the tick.
            logger.warning(
                "obsidian-watcher: scan of %s failed: %s",
                vault["path"], exc,
            )
    return scanned


async def watcher_loop(
    *,
    tick_seconds: int = DEFAULT_TICK_SECONDS,
    scan_fn: ScanFn | None = None,
    scan_timeout_s: float = DEFAULT_SCAN_TIMEOUT_S,
) -> None:
    """Long-running scheduler. Wrap in `asyncio.create_task` from the
    FastAPI lifespan and `task.cancel()` on shutdown."""
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            await _tick_once(scan_fn=scan_fn, timeout_s=scan_timeout_s)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover
            logger.warning("obsidian-watcher tick failed: %s", exc)


# ---------------------------------------------------------------------
# Retrieval helpers — used by the orchestrator when a project opts
# into `obsidian_source`. Kept here so the query logic lives next to
# the index it reads from.
# ---------------------------------------------------------------------

def search_watched_vaults(
    query: str, *, limit: int = 10,
) -> list[dict]:
    """Light semantic-ish search over every watched vault. Defers to
    `vault_search` so the TF-IDF cache is shared with the UI."""
    from .vault_search import semantic_search  # noqa: PLC0415

    q = (query or "").strip()
    if not q:
        return []
    hits: list[dict] = []
    for v in store.list_watched_vaults():
        if not v.get("enabled"):
            continue
        try:
            vault_hits = semantic_search(v["path"], q, limit=limit)
        except Exception as exc:
            logger.warning("semantic_search on %s failed: %s", v["path"], exc)
            continue
        for h in vault_hits:
            h["vault_path"] = v["path"]
        hits.extend(vault_hits)
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits[:limit]


# Exposed so tests and the /api/obsidian/status endpoint can reset state.
def _reset_for_test() -> None:
    _SCAN_LOCKS.clear()
    _SCAN_BUSY.clear()
