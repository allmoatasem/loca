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


def test_pdf_can_handle(tmp_path):
    from src.importers.adapters.pdf import PDFAdapter
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert PDFAdapter().can_handle(f) is True


def test_pdf_cannot_handle_md(tmp_path):
    from src.importers.adapters.pdf import PDFAdapter
    f = tmp_path / "doc.md"
    f.write_text("hello")
    assert PDFAdapter().can_handle(f) is False


def test_epub_can_handle(tmp_path):
    from src.importers.adapters.epub import EpubAdapter
    f = tmp_path / "book.epub"
    f.write_bytes(b"PK")
    assert EpubAdapter().can_handle(f) is True


def test_docx_can_handle(tmp_path):
    from src.importers.adapters.docx import DocxAdapter
    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK")
    assert DocxAdapter().can_handle(f) is True


def test_spreadsheet_can_handle_csv(tmp_path):
    from src.importers.adapters.spreadsheet import SpreadsheetAdapter
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25")
    assert SpreadsheetAdapter().can_handle(f) is True


def test_spreadsheet_extracts_csv_rows(tmp_path):
    from src.importers.adapters.spreadsheet import SpreadsheetAdapter
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25")
    chunks = SpreadsheetAdapter().extract(f)
    assert len(chunks) == 2
    assert "Alice" in chunks[0].text
    assert "age: 30" in chunks[0].text


def test_json_adapter_can_handle(tmp_path):
    from src.importers.adapters.json_adapter import JSONAdapter
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    assert JSONAdapter().can_handle(f) is True


def test_json_adapter_extracts_text(tmp_path):
    from src.importers.adapters.json_adapter import JSONAdapter
    f = tmp_path / "data.json"
    f.write_text('{"name": "Alice", "role": "engineer"}')
    chunks = JSONAdapter().extract(f)
    assert len(chunks) >= 1
    assert "Alice" in chunks[0].text


def test_web_adapter_can_handle_url():
    from src.importers.adapters.web import WebAdapter
    assert WebAdapter().can_handle(Path("https://example.com")) is True
    assert WebAdapter().can_handle(Path("http://example.com")) is True
    assert WebAdapter().can_handle(Path("/local/file.md")) is False


def test_image_can_handle(tmp_path):
    from src.importers.adapters.image import ImageAdapter
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    assert ImageAdapter().can_handle(f) is True


def test_image_skips_gracefully_without_vision_model(tmp_path):
    from src.importers.adapters.image import ImageAdapter
    f = tmp_path / "photo.png"
    f.write_bytes(b"\x89PNG")
    chunks = ImageAdapter().extract(f)
    assert isinstance(chunks, list)
