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
        assert len(out) <= PROMPT_CHAR_BUDGET + 400  # tolerate wrapper header/footer + citation instruction
        # Truncation marker should be present
        assert "..." in out or "…" in out

    def test_total_budget_capped_under_many_chunks(self):
        # 50 chunks of 600 chars each = 30k chars if naively concatenated
        mems = [{"content": "C" * 600} for _ in range(50)]
        out = self.plugin.format_for_prompt(mems)
        assert len(out) <= PROMPT_CHAR_BUDGET + 400

    def test_top_ranked_memories_prioritised(self):
        # First (most relevant) memory should appear even when later ones are dropped
        mems = [{"content": "UNIQUE_TOP_MARKER"}]
        mems.extend({"content": "C" * 600} for _ in range(50))
        out = self.plugin.format_for_prompt(mems)
        assert "UNIQUE_TOP_MARKER" in out

    def test_memories_are_numbered_for_citation(self):
        mems = [{"content": "Alpha"}, {"content": "Beta"}, {"content": "Gamma"}]
        out = self.plugin.format_for_prompt(mems)
        assert "[memory: 1]" in out
        assert "[memory: 2]" in out
        assert "[memory: 3]" in out

    def test_citation_instruction_included_in_wrapper(self):
        mems = [{"content": "Alpha"}]
        out = self.plugin.format_for_prompt(mems)
        # The wrapper should tell the model to cite memories it uses
        assert "[memory:" in out
        assert "cite" in out.lower()
