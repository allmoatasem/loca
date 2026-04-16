# tests/test_importers/test_base.py
from src.importers.base import BaseAdapter, Chunk, ImportResult


def test_chunk_defaults():
    c = Chunk(text="hello", source="test", title="t", created_at="", metadata={})
    assert c.text == "hello"
    assert c.source == "test"
    assert c.metadata == {}


def test_import_result_fields():
    r = ImportResult(total=10, stored=8, skipped=2, source="test")
    assert r.total == 10
    assert r.stored + r.skipped == r.total


def test_base_adapter_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        BaseAdapter()  # cannot instantiate abstract class


def test_concrete_adapter_must_implement_all_methods():
    class Bad(BaseAdapter):
        pass  # missing all three methods
    import pytest
    with pytest.raises(TypeError):
        Bad()


