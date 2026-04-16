from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.importers.base import BaseAdapter, Chunk
from src.importers.service import ImportService


class FakeAdapter(BaseAdapter):
    source_name = "fake"

    def can_handle(self, path: Path) -> bool:
        return path.suffix == ".fake"

    def extract(self, path: Path) -> list[Chunk]:
        return [Chunk(text="hello world", source="fake", title="test", created_at="", metadata={})]


def _make_service(collection=None):
    plugin = MagicMock()
    plugin.collection = collection or MagicMock()
    plugin.palace_path = "/fake/palace"
    plugin._available = True
    svc = ImportService(memory_plugin=plugin)
    svc.register(FakeAdapter())
    return svc


def test_register_adapter():
    svc = _make_service()
    assert any(a.source_name == "fake" for a in svc._adapters)


def test_detect_adapter_by_extension(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    svc = _make_service()
    adapter = svc._detect(f)
    assert adapter is not None
    assert adapter.source_name == "fake"


def test_detect_returns_none_for_unknown(tmp_path):
    f = tmp_path / "data.xyz"
    f.write_text("x")
    svc = _make_service()
    assert svc._detect(f) is None


def test_content_hash_is_sha256():
    from src.importers.service import _content_hash
    text = "hello"
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert _content_hash(text) == expected


@pytest.mark.asyncio
async def test_run_yields_progress_and_done(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    collection = MagicMock()
    collection.get.return_value = {"ids": []}  # no duplicates
    svc = _make_service(collection=collection)
    events = []
    with patch("src.importers.service._store_chunk"), \
         patch("src.importers.service.add_import_record"):
        async for event in svc.run(f):
            events.append(event)
    statuses = [e["status"] for e in events]
    assert "extracting" in statuses
    assert events[-1]["status"] == "done"
    assert events[-1]["stored"] >= 0


@pytest.mark.asyncio
async def test_duplicate_chunk_is_skipped(tmp_path):
    f = tmp_path / "data.fake"
    f.write_text("x")
    collection = MagicMock()
    collection.get.return_value = {"ids": ["existing-id"]}  # hash exists
    svc = _make_service(collection=collection)
    events = []
    with patch("src.importers.service._store_chunk") as mock_store, \
         patch("src.importers.service.add_import_record"):
        async for event in svc.run(f):
            events.append(event)
    mock_store.assert_not_called()
    done = next(e for e in events if e["status"] == "done")
    assert done["skipped"] == 1
    assert done["stored"] == 0


def test_build_default_service_has_all_adapters():
    from unittest.mock import MagicMock

    from src.importers.service import build_default_service
    plugin = MagicMock()
    plugin.collection = MagicMock()
    plugin.palace_path = "/fake/palace"
    plugin._available = True
    svc = build_default_service(plugin)
    names = {a.source_name for a in svc._adapters}
    assert "anthropic" in names
    assert "openai" in names
    assert "markdown" in names
    assert "pdf" in names
    assert "directory" in names
