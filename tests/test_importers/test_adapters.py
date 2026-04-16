from pathlib import Path

from src.importers.adapters.anthropic import AnthropicAdapter

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "anthropic_export"


def test_anthropic_can_handle_export_dir():
    adapter = AnthropicAdapter()
    assert adapter.can_handle(FIXTURE_DIR) is True


def test_anthropic_cannot_handle_single_file(tmp_path):
    f = tmp_path / "conversations.json"
    f.write_text("[]")
    adapter = AnthropicAdapter()
    assert adapter.can_handle(f) is False  # file not dir


def test_anthropic_extracts_conversation_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    conv_chunks = [c for c in chunks if c.metadata.get("type") == "conversation"]
    assert len(conv_chunks) >= 1
    assert "Hello Claude" in conv_chunks[0].text
    assert "Hello! How can I help?" in conv_chunks[0].text


def test_anthropic_extracts_memory_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    mem_chunks = [c for c in chunks if c.metadata.get("type") == "memory"]
    assert len(mem_chunks) >= 1
    assert any("Preferences" in c.text or "Python" in c.text for c in mem_chunks)


def test_anthropic_extracts_project_doc_chunks():
    adapter = AnthropicAdapter()
    chunks = adapter.extract(FIXTURE_DIR)
    doc_chunks = [c for c in chunks if c.metadata.get("type") == "project_doc"]
    assert len(doc_chunks) >= 1
    assert "project notes" in doc_chunks[0].text.lower()


def test_anthropic_source_name():
    assert AnthropicAdapter().source_name == "anthropic"
