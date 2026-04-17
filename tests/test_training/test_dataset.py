"""Tests for src.training.dataset."""
from __future__ import annotations

import json

from src.training.dataset import build_chat_examples, write_split


def _example(user: str, reply: str) -> dict:
    return {"messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": reply},
    ]}


def test_write_split_creates_three_files(tmp_path):
    examples = [_example(f"q{i}", f"a{i}") for i in range(10)]
    counts = write_split(examples, tmp_path)
    for name in ("train", "valid", "test"):
        assert (tmp_path / f"{name}.jsonl").exists()
    assert sum(counts.values()) == 10


def test_write_split_default_proportions(tmp_path):
    examples = [_example(f"q{i}", f"a{i}") for i in range(100)]
    counts = write_split(examples, tmp_path)
    assert counts == {"train": 80, "valid": 10, "test": 10}


def test_write_split_is_deterministic(tmp_path):
    examples = [_example(f"q{i}", f"a{i}") for i in range(25)]
    write_split(examples, tmp_path / "a", seed=7)
    write_split(examples, tmp_path / "b", seed=7)
    assert (tmp_path / "a" / "train.jsonl").read_text() == (tmp_path / "b" / "train.jsonl").read_text()
    assert (tmp_path / "a" / "valid.jsonl").read_text() == (tmp_path / "b" / "valid.jsonl").read_text()


def test_write_split_lines_are_valid_json(tmp_path):
    examples = [_example(f"q{i}", f"a{i}") for i in range(5)]
    write_split(examples, tmp_path)
    for name in ("train", "valid", "test"):
        for line in (tmp_path / f"{name}.jsonl").read_text().splitlines():
            row = json.loads(line)
            assert "messages" in row
            assert row["messages"][0]["role"] in ("user", "assistant")


def test_write_split_rejects_bad_fracs(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        write_split([], tmp_path, train_frac=0.9, valid_frac=0.2)


def test_build_chat_examples_skips_short_conversations(monkeypatch):
    def fake_list(limit=10000):
        return [{"id": "a"}, {"id": "b"}]

    def fake_get(cid):
        if cid == "a":
            return {"messages": [{"role": "user", "content": "hi"}]}
        return {"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
        ]}

    monkeypatch.setattr("src.training.dataset.list_conversations", fake_list)
    monkeypatch.setattr("src.training.dataset.get_conversation", fake_get)

    examples = build_chat_examples(min_turns=2)
    assert len(examples) == 1
    assert examples[0]["messages"][0]["content"] == "hello"


def test_build_chat_examples_filters_empty_and_non_string_content(monkeypatch):
    def fake_list(limit=10000):
        return [{"id": "x"}]

    def fake_get(cid):
        return {"messages": [
            {"role": "user", "content": "  "},                 # whitespace → drop
            {"role": "user", "content": ["list", "content"]},  # non-string → drop
            {"role": "system", "content": "ignored"},          # system → drop
            {"role": "user", "content": "real question"},
            {"role": "assistant", "content": "real answer"},
        ]}

    monkeypatch.setattr("src.training.dataset.list_conversations", fake_list)
    monkeypatch.setattr("src.training.dataset.get_conversation", fake_get)

    examples = build_chat_examples(min_turns=2)
    assert len(examples) == 1
    assert [m["content"] for m in examples[0]["messages"]] == ["real question", "real answer"]
