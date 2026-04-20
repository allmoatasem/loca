"""Unit tests for the app-level Obsidian Watcher.

The watcher is responsible for:
- registering / unregistering vaults (with path validation)
- running a scan via an injectable callable (no real vault on disk)
- enforcing per-vault re-entrancy via an asyncio.Lock
- skipping vaults whose last_scan_at is still within scan_interval_s
- persisting the scanner's stats dict onto the row

These tests inject a fake `scan_fn` so we don't need the real
Obsidian indexer or a temp filesystem vault.
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.obsidian_watcher as watcher  # noqa: E402
import src.store as store_module  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_path = tmp_path / "test_loca.db"
    with patch.object(store_module, "_DB_PATH", db_path):
        watcher._reset_for_test()
        yield db_path
        watcher._reset_for_test()


def _insert_vault_row(path: str, *, interval: int = 300, last: float | None = None) -> None:
    """Bypass register() (which validates on disk) so we can test the
    loop logic without a real Obsidian vault fixture."""
    store_module.upsert_watched_vault(path, name="fixture", scan_interval_s=interval)
    if last is not None:
        store_module.mark_watched_vault_scanned(path, {"total": 0})
        with store_module._conn() as c:  # noqa: SLF001
            c.execute(
                "UPDATE watched_vaults SET last_scan_at=? WHERE path=?",
                (last, path),
            )
            c.commit()


def _stub_scanner(stats: dict):
    def _scan(_path: str) -> dict:
        return stats
    return _scan


@pytest.mark.asyncio
async def test_scan_now_persists_stats(tmp_path):
    path = str(tmp_path / "vault")
    _insert_vault_row(path)
    stats = {"total": 4, "added": 2, "updated": 1, "skipped": 1, "removed": 0, "errors": 0}

    result = await watcher.scan_now(path, scan_fn=_stub_scanner(stats))

    assert result["path"] == path
    assert result["total"] == 4
    row = store_module.get_watched_vault(path)
    assert row is not None
    assert row["last_scan_at"] is not None
    assert row["last_stats"]["added"] == 2


@pytest.mark.asyncio
async def test_scan_now_marks_busy_during_run(tmp_path):
    path = str(tmp_path / "vault")
    _insert_vault_row(path)

    observed: dict = {"busy": False}

    def _slow_scan(_p: str) -> dict:
        # Peek at busy state from the worker thread — if the watcher
        # failed to mark busy before dispatching to the thread, this
        # would stay False.
        observed["busy"] = watcher.is_busy(path)
        return {"total": 0}

    await watcher.scan_now(path, scan_fn=_slow_scan)
    assert observed["busy"] is True
    # But the set clears once the scan returns.
    assert not watcher.is_busy(path)


@pytest.mark.asyncio
async def test_tick_skips_not_due_vaults(tmp_path):
    import time

    path = str(tmp_path / "vault")
    # last_scan_at = now → not due for at least scan_interval_s seconds.
    _insert_vault_row(path, interval=300, last=time.time())

    calls = {"n": 0}

    def _scan(_p: str) -> dict:
        calls["n"] += 1
        return {"total": 0}

    scanned = await watcher._tick_once(scan_fn=_scan)
    assert scanned == 0
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_tick_runs_due_vault(tmp_path):
    path = str(tmp_path / "vault")
    # No last_scan_at → due immediately.
    _insert_vault_row(path, interval=60)

    scanned = await watcher._tick_once(scan_fn=_stub_scanner({"total": 1}))
    assert scanned == 1
    row = store_module.get_watched_vault(path)
    assert row and row["last_scan_at"] is not None


@pytest.mark.asyncio
async def test_tick_isolates_failing_vault(tmp_path):
    good = str(tmp_path / "good")
    bad = str(tmp_path / "bad")
    _insert_vault_row(good)
    _insert_vault_row(bad)

    def _scan(path: str) -> dict:
        if path == bad:
            raise RuntimeError("boom")
        return {"total": 1}

    scanned = await watcher._tick_once(scan_fn=_scan)
    # Good vault still got through despite bad's failure.
    assert scanned == 1
    good_row = store_module.get_watched_vault(good)
    assert good_row and good_row["last_scan_at"] is not None


@pytest.mark.asyncio
async def test_unregister_removes_row(tmp_path):
    path = str(tmp_path / "vault")
    _insert_vault_row(path)
    assert store_module.get_watched_vault(path) is not None
    watcher.unregister(path)
    assert store_module.get_watched_vault(path) is None


@pytest.mark.asyncio
async def test_register_rejects_invalid_path(tmp_path):
    bogus = str(tmp_path / "not-a-vault")
    with pytest.raises(ValueError):
        watcher.register(bogus)


@pytest.mark.asyncio
async def test_scan_now_releases_lock_on_error(tmp_path):
    path = str(tmp_path / "vault")
    _insert_vault_row(path)

    def _boom(_p: str) -> dict:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        await watcher.scan_now(path, scan_fn=_boom)
    # The lock must be released so a follow-up scan can run.
    assert not watcher.is_busy(path)
    # And a second call must actually dispatch the scanner again.
    result = await watcher.scan_now(path, scan_fn=_stub_scanner({"total": 2}))
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_concurrent_scans_same_vault_serialize(tmp_path):
    path = str(tmp_path / "vault")
    _insert_vault_row(path)

    running = {"count": 0, "max": 0}
    barrier = asyncio.Event()

    async def _slow_scanner():
        running["count"] += 1
        running["max"] = max(running["max"], running["count"])
        await asyncio.sleep(0.05)
        running["count"] -= 1
        return {"total": 0}

    def _scan(_p: str) -> dict:
        # `scan_now` dispatches via to_thread, so we must convert the
        # async primitive back to sync with a local loop hop.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_slow_scanner())
        finally:
            loop.close()

    t1 = asyncio.create_task(watcher.scan_now(path, scan_fn=_scan))
    t2 = asyncio.create_task(watcher.scan_now(path, scan_fn=_scan))
    await asyncio.gather(t1, t2)
    barrier.set()
    # Lock is per-path, so max concurrent scanners on the same vault
    # must be 1 — not 2.
    assert running["max"] == 1
