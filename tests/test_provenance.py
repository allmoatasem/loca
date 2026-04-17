"""Tests for src/provenance.py — retrieval audit + citation verification."""
from __future__ import annotations

import json

import pytest

from src.provenance import (
    Provenance,
    RetrievedMemory,
    extract_citations,
    verify_citations,
    write_provenance,
)

# ---------------------------------------------------------------------------
# extract_citations
# ---------------------------------------------------------------------------

def test_extract_citations_parses_inline_tags():
    text = "The user prefers Python [memory: 1] and uses tabs [memory: 3]."
    assert extract_citations(text) == [1, 3]


def test_extract_citations_handles_no_citations():
    assert extract_citations("No citations here.") == []


def test_extract_citations_dedupes_repeated():
    text = "per [memory: 2] and again [memory: 2] plus [memory: 5]"
    assert extract_citations(text) == [2, 5]


def test_extract_citations_ignores_unrelated_bracket_contents():
    text = "[memory: alpha] is malformed. Also [todo: 1]. Real one: [memory: 7]."
    assert extract_citations(text) == [7]


# ---------------------------------------------------------------------------
# verify_citations
# ---------------------------------------------------------------------------

def test_verify_no_phantoms_returns_empty_list():
    text = "Loca prefers Python [memory: 1]."
    phantoms = verify_citations(text, retrieved_count=3)
    assert phantoms == []


def test_verify_flags_citations_past_retrieved_count():
    text = "Per [memory: 1] and [memory: 9]."
    phantoms = verify_citations(text, retrieved_count=5)
    assert phantoms == [9]


def test_verify_flags_zero_as_phantom():
    # Memory IDs are 1-indexed when the model sees them.
    text = "According to [memory: 0]."
    phantoms = verify_citations(text, retrieved_count=5)
    assert phantoms == [0]


def test_verify_empty_retrieved_flags_every_citation():
    text = "No memories at all, yet [memory: 1] appeared."
    phantoms = verify_citations(text, retrieved_count=0)
    assert phantoms == [1]


# ---------------------------------------------------------------------------
# write_provenance
# ---------------------------------------------------------------------------

def _sample_provenance(**overrides) -> Provenance:
    defaults = dict(
        conv_id="demo-conv",
        user_query="what do you know about me",
        recall_query="what do you know about me. Everything.",
        expanded_queries=[
            "what do you know about me",
            "user's profession",
            "user's interests",
        ],
        retrieved=[
            RetrievedMemory(index=1, id="m1", score=4.2, content="Senior SEIT at JLR"),
            RetrievedMemory(index=2, id="m2", score=3.1, content="Learning Python"),
        ],
        cited=[1],
        phantoms=[9],
    )
    defaults.update(overrides)
    return Provenance(**defaults)


def test_write_provenance_creates_markdown_file(tmp_path):
    prov = _sample_provenance()
    path = write_provenance(prov, root=tmp_path)
    assert path.exists()
    assert path.suffix == ".md"
    # Daily subdir by date (keeps folder browsable)
    assert path.parent.parent == tmp_path
    text = path.read_text()
    assert "what do you know about me" in text
    assert "Senior SEIT at JLR" in text
    assert "[memory: 1]" in text           # cited marker
    assert "[memory: 2]" in text           # retrieved but not cited
    assert "phantom" in text.lower()       # phantom section exists
    assert "[memory: 9]" in text


def test_write_provenance_includes_conv_id_in_filename(tmp_path):
    prov = _sample_provenance(conv_id="xyz-42")
    path = write_provenance(prov, root=tmp_path)
    assert "xyz-42" in path.name


def test_write_provenance_handles_adhoc_conv(tmp_path):
    """Turns outside a saved conversation still get a sidecar."""
    prov = _sample_provenance(conv_id=None)
    path = write_provenance(prov, root=tmp_path)
    assert path.exists()
    assert "adhoc" in path.name


def test_write_provenance_is_idempotent_within_a_second(tmp_path):
    """Two writes in the same ISO second share a filename; last write wins.

    This is intentional: provenance is best-effort, never blocks a response,
    and losing one of two near-simultaneous turns is preferable to an
    ever-growing filename collision suffix scheme.
    """
    prov = _sample_provenance()
    p1 = write_provenance(prov, root=tmp_path)
    p2 = write_provenance(prov, root=tmp_path)
    assert p1 == p2


def test_provenance_roundtrip_json(tmp_path):
    """The Provenance dataclass should survive JSON serialisation for
    lightweight analytics in the future."""
    prov = _sample_provenance()
    blob = json.dumps(prov.to_dict())
    restored = Provenance.from_dict(json.loads(blob))
    assert restored.user_query == prov.user_query
    assert restored.retrieved[0].content == prov.retrieved[0].content
    assert restored.phantoms == prov.phantoms


# ---------------------------------------------------------------------------
# Verifier footer helper
# ---------------------------------------------------------------------------

from src.provenance import append_verifier_footer  # noqa: E402


def test_append_verifier_footer_no_phantoms_returns_text_unchanged():
    text = "Per [memory: 1] Loca prefers Python."
    assert append_verifier_footer(text, phantoms=[]) == text


def test_append_verifier_footer_with_phantoms_adds_paragraph():
    text = "Per [memory: 9], the user likes Go."
    out = append_verifier_footer(text, phantoms=[9])
    assert text in out
    assert "9" in out
    assert out != text
    assert "hallucination" in out.lower() or "phantom" in out.lower() or "no such" in out.lower()


def test_append_verifier_footer_formats_multiple_phantoms():
    out = append_verifier_footer("body", phantoms=[5, 12])
    assert "5" in out and "12" in out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_extract_citations_tolerates_whitespace():
    text = "Per [memory:  4 ] and [memory:7]."
    assert extract_citations(text) == [4, 7]


@pytest.mark.parametrize("text, expected", [
    ("", []),
    ("[memory: 1] [memory: 2] [memory: 3]", [1, 2, 3]),
    ("Line 1 [memory: 1]\nLine 2 [memory: 2]", [1, 2]),
])
def test_extract_citations_parametrised(text, expected):
    assert extract_citations(text) == expected
