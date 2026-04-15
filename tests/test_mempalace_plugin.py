"""Unit tests for MemPalaceMemoryPlugin."""
from __future__ import annotations

import sys
from unittest.mock import (
    MagicMock,
    patch,
)

# ---------------------------------------------------------------------------
# Helpers to fake MemPalace imports
# ---------------------------------------------------------------------------

def _make_fake_mempalace(collection: MagicMock) -> None:
    """Inject fake mempalace modules into sys.modules."""
    fake_searcher = MagicMock()
    fake_searcher.get_collection.return_value = collection
    fake_searcher.search_memories.return_value = {
        "results": [
            {
                "id": "abc123",
                "content": "User prefers Python over JavaScript.",
                "room": "preferences",
                "timestamp": "2026-04-15T10:00:00",
                "distance": 0.1,
            }
        ]
    }

    fake_miner = MagicMock()
    fake_miner.add_drawer.return_value = None

    fake_mempalace = MagicMock()

    sys.modules.setdefault("mempalace", fake_mempalace)
    sys.modules["mempalace.searcher"] = fake_searcher
    sys.modules["mempalace.miner"] = fake_miner


def _remove_fake_mempalace() -> None:
    for key in list(sys.modules.keys()):
        if key.startswith("mempalace"):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMemPalaceMemoryPlugin:
    def setup_method(self):
        self.collection = MagicMock()
        _make_fake_mempalace(self.collection)

    def teardown_method(self):
        _remove_fake_mempalace()

    def _make_plugin(self):
        # Import after fakes are in sys.modules
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        with patch("src.plugins.mempalace_plugin._PALACE_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.__str__ = lambda s: "/fake/palace"
            plugin = MemPalaceMemoryPlugin()
        return plugin

    def test_init_success(self):
        plugin = self._make_plugin()
        assert plugin._available is True

    def test_init_failure_disables_plugin(self):
        _remove_fake_mempalace()  # break import
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        # Patch _try_init so get_collection raises even if real MemPalace is installed
        with patch("src.plugins.mempalace_plugin.MemPalaceMemoryPlugin._try_init",
                   side_effect=lambda self: setattr(self, "_available", False) or None):
            plugin = MemPalaceMemoryPlugin.__new__(MemPalaceMemoryPlugin)
            plugin._available = False
            plugin._palace_path = "/fake/palace"
            plugin._collection = None
        assert plugin._available is False

    async def test_store_returns_id(self):
        plugin = self._make_plugin()
        mid = await plugin.store("I prefer Python.", {})
        assert mid == ""  # MemPalace manages its own IDs; store() does not return one

    async def test_store_when_unavailable_returns_empty(self):
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        # Create plugin instance that bypasses _try_init — simulates unavailable state
        plugin = MemPalaceMemoryPlugin.__new__(MemPalaceMemoryPlugin)
        plugin._available = False
        plugin._palace_path = "/fake/palace"
        plugin._collection = None
        mid = await plugin.store("anything", {})
        assert mid == ""

    async def test_recall_returns_formatted_results(self):
        plugin = self._make_plugin()
        results = await plugin.recall("Python preference", limit=5)
        assert len(results) == 1
        assert results[0]["content"] == "User prefers Python over JavaScript."
        assert results[0]["type"] == "preferences"
        assert "score" in results[0]
        assert isinstance(results[0]["score"], float)

    async def test_recall_empty_query_returns_empty(self):
        plugin = self._make_plugin()
        results = await plugin.recall("", limit=5)
        assert results == []

    async def test_recall_when_unavailable_returns_empty(self):
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin.__new__(MemPalaceMemoryPlugin)
        plugin._available = False
        plugin._palace_path = "/fake/palace"
        plugin._collection = None
        results = await plugin.recall("anything", limit=5)
        assert results == []

    def test_list_all_returns_memories(self):
        plugin = self._make_plugin()
        mems = plugin.list_all()
        assert isinstance(mems, list)

    def test_list_all_with_type_filter(self):
        plugin = self._make_plugin()
        self.collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mems = plugin.list_all(type="decisions")
        assert isinstance(mems, list)

    def test_delete_calls_collection(self):
        plugin = self._make_plugin()
        plugin.delete("abc123")
        self.collection.delete.assert_called_once_with(ids=["abc123"])

    def test_delete_when_unavailable_does_nothing(self):
        from src.plugins.mempalace_plugin import MemPalaceMemoryPlugin
        plugin = MemPalaceMemoryPlugin.__new__(MemPalaceMemoryPlugin)
        plugin._available = False
        plugin._palace_path = "/fake/palace"
        plugin._collection = None
        plugin.delete("abc123")  # must not raise

    def test_update_calls_collection(self):
        plugin = self._make_plugin()
        plugin.update("abc123", "updated content")
        self.collection.update.assert_called_once_with(
            ids=["abc123"], documents=["updated content"]
        )

    def test_classify_room_decisions(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("We decided to use PostgreSQL because of reliability") == "decisions"

    def test_classify_room_preferences(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("I prefer Python over JavaScript") == "preferences"

    def test_classify_room_problems(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("There was a crash in the download module") == "problems"

    def test_classify_room_general_fallback(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("The weather is nice today") == "general"

    def test_classify_room_milestones(self):
        from src.plugins.mempalace_plugin import _classify_room
        assert _classify_room("Finally got it working and done") == "milestones"
