"""Tests for src.training.cli (argument parsing and error paths)."""
from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

from src.training.cli import cmd_build, cmd_train


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_cmd_build_writes_splits(tmp_path, monkeypatch):
    def fake_examples(min_turns=2):
        return [{"messages": [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ]} for i in range(10)]

    monkeypatch.setattr("src.training.cli.build_chat_examples", fake_examples)
    rc = cmd_build(_ns(out=str(tmp_path), min_turns=2, seed=42))
    assert rc == 0
    for name in ("train", "valid", "test"):
        assert (tmp_path / f"{name}.jsonl").exists()


def test_cmd_build_exits_2_when_no_examples(tmp_path, monkeypatch):
    monkeypatch.setattr("src.training.cli.build_chat_examples", lambda min_turns=2: [])
    rc = cmd_build(_ns(out=str(tmp_path), min_turns=2, seed=42))
    assert rc == 2


def test_cmd_train_exits_2_when_no_train_jsonl(tmp_path):
    rc = cmd_train(_ns(
        model="/fake/model", data=str(tmp_path),
        iters=1000, adapter_out="./loca-adapter",
    ))
    assert rc == 2


def test_cmd_train_shells_out_to_mlx_lm_lora(tmp_path):
    (tmp_path / "train.jsonl").write_text("{}\n")
    captured: list = []
    def fake_call(cmd, **kwargs):
        captured.append(cmd)
        return 0
    with patch("src.training.cli.subprocess.call", side_effect=fake_call):
        rc = cmd_train(_ns(
            model="/fake/model", data=str(tmp_path),
            iters=500, adapter_out=str(tmp_path / "adapter"),
        ))
    assert rc == 0
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "mlx_lm.lora"]
    assert "--train" in cmd
    assert "--model" in cmd
    assert "--data" in cmd
    assert "--iters" in cmd and "500" in cmd
