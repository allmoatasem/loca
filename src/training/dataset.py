"""Build MLX-LoRA-ready JSONL datasets from Loca's stored conversations.

Produces `train.jsonl`, `valid.jsonl`, and `test.jsonl` in the layout
expected by `mlx_lm.lora --data <dir>`. Each line is a chat example:

    {"messages": [{"role": "user", "content": "…"},
                  {"role": "assistant", "content": "…"}]}

Intentionally narrow: this is the foundation piece. Dataset curation,
quality gating, and inference-time adapter loading are out of scope and
will land in follow-up PRs once fine-tuning is validated against
specific RAG failure cases (see project_finetuning_position memory).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from ..store import get_conversation, list_conversations


def build_chat_examples(min_turns: int = 2) -> list[dict]:
    """Pull conversations from loca.db and return chat-formatted examples.

    Drops conversations shorter than `min_turns` (too little signal). Only
    user / assistant messages with non-empty string content are kept.
    """
    examples: list[dict] = []
    for meta in list_conversations(limit=10000):
        conv = get_conversation(meta["id"])
        if not conv:
            continue
        msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in conv.get("messages", [])
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ]
        if len(msgs) >= min_turns:
            examples.append({"messages": msgs})
    return examples


def write_split(
    examples: list[dict],
    out_dir: Path,
    seed: int = 42,
    train_frac: float = 0.8,
    valid_frac: float = 0.1,
) -> dict[str, int]:
    """Shuffle deterministically and write train/valid/test JSONL files.

    Returns the example count in each split. The test-set fraction is
    implicit: `1 - train_frac - valid_frac`.
    """
    if train_frac + valid_frac > 1.0:
        raise ValueError("train_frac + valid_frac must be <= 1.0")
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    shuffled = list(examples)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = int(n * train_frac)
    n_valid = int(n * valid_frac)
    splits = {
        "train": shuffled[:n_train],
        "valid": shuffled[n_train:n_train + n_valid],
        "test": shuffled[n_train + n_valid:],
    }
    for name, rows in splits.items():
        path = out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False))
                f.write("\n")
    return {name: len(rows) for name, rows in splits.items()}
