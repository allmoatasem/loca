"""Tests for shared logic on the MemoryPlugin base class (notably format_for_prompt)."""
from __future__ import annotations

from src.plugins.memory_plugin import (
    PER_MEMORY_CHAR_MAX,
    PROMPT_CHAR_BUDGET,
    MemoryPlugin,
)


class _StubPlugin(MemoryPlugin):
    """Minimal concrete subclass so we can exercise the base-class format_for_prompt."""
    async def store(self, text, metadata):  # pragma: no cover - unused
        return ""

    async def recall(self, query, limit=20):  # pragma: no cover - unused
        return []

    def list_all(self, type=None):  # pragma: no cover - unused
        return []

    def list_paged(self, type=None, limit=50, offset=0):  # pragma: no cover - unused
        return {"items": [], "total": 0}

    def delete(self, mem_id):  # pragma: no cover - unused
        pass

    def update(self, mem_id, content):  # pragma: no cover - unused
        pass


class TestFormatForPrompt:
    def setup_method(self):
        self.plugin = _StubPlugin()

    def test_empty_returns_empty(self):
        assert self.plugin.format_for_prompt([]) == ""

    def test_small_memories_included_fully(self):
        mems = [{"content": "User prefers Python."}, {"content": "User is a Senior SEIT."}]
        out = self.plugin.format_for_prompt(mems)
        assert "User prefers Python." in out
        assert "User is a Senior SEIT." in out

    def test_single_huge_memory_is_truncated(self):
        huge = "X" * (PER_MEMORY_CHAR_MAX * 5)
        out = self.plugin.format_for_prompt([{"content": huge}])
        assert len(out) <= PROMPT_CHAR_BUDGET + 200  # tolerate wrapper header/footer
        # Truncation marker should be present
        assert "..." in out or "…" in out

    def test_total_budget_capped_under_many_chunks(self):
        # 50 chunks of 600 chars each = 30k chars if naively concatenated
        mems = [{"content": "C" * 600} for _ in range(50)]
        out = self.plugin.format_for_prompt(mems)
        assert len(out) <= PROMPT_CHAR_BUDGET + 200

    def test_top_ranked_memories_prioritised(self):
        # First (most relevant) memory should appear even when later ones are dropped
        mems = [{"content": "UNIQUE_TOP_MARKER"}]
        mems.extend({"content": "C" * 600} for _ in range(50))
        out = self.plugin.format_for_prompt(mems)
        assert "UNIQUE_TOP_MARKER" in out
