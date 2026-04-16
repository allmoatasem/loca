from pathlib import Path

from src.importers.adapters.anthropic import AnthropicAdapter
from src.importers.adapters.markdown import MarkdownAdapter

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "anthropic_export"
SAMPLE_MD = Path(__file__).parent.parent / "fixtures" / "sample.md"


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


def test_markdown_can_handle_md_file():
    assert MarkdownAdapter().can_handle(SAMPLE_MD) is True


def test_markdown_cannot_handle_dir(tmp_path):
    assert MarkdownAdapter().can_handle(tmp_path) is False


def test_markdown_cannot_handle_pdf(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF")
    assert MarkdownAdapter().can_handle(f) is False


def test_markdown_chunks_by_heading():
    chunks = MarkdownAdapter().extract(SAMPLE_MD)
    assert len(chunks) >= 2
    texts = " ".join(c.text for c in chunks)
    assert "Section One" in texts
    assert "Section Two" in texts


def test_markdown_fallback_no_headings(tmp_path):
    f = tmp_path / "flat.md"
    f.write_text("No headings here. Just a flat block of text.")
    chunks = MarkdownAdapter().extract(f)
    assert len(chunks) == 1
    assert "flat block" in chunks[0].text


def test_openai_can_handle_export_with_mapping(tmp_path):
    from src.importers.adapters.openai import OpenAIAdapter
    (tmp_path / "conversations.json").write_text(
        '[{"id": "c1", "title": "t", "mapping": {}, "create_time": 0}]'
    )
    assert OpenAIAdapter().can_handle(tmp_path) is True


def test_openai_cannot_handle_anthropic_export():
    from src.importers.adapters.openai import OpenAIAdapter
    assert OpenAIAdapter().can_handle(FIXTURE_DIR) is False
