"""CLI for the Loca fine-tuning foundation.

Two subcommands:

  build  — Build a train/valid/test JSONL dataset from loca.db conversations
  train  — Shell out to mlx_lm.lora with the built dataset and a base model

Deliberately thin. Not in this PR: UI integration, inference-time adapter
loading, evaluation harness. See project_finetuning_position memory for why
fine-tuning is gated on concrete RAG failure cases rather than shipped by
default.

Usage:
  python -m src.training.cli build --out ./training-data
  python -m src.training.cli train \\
      --model ~/loca_models/mlx/qwen2.5-7b-instruct \\
      --data ./training-data \\
      --iters 1000
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .dataset import build_chat_examples, write_split


def cmd_build(args: argparse.Namespace) -> int:
    examples = build_chat_examples(min_turns=args.min_turns)
    if not examples:
        print(
            "No conversations found in loca.db (or all were below the min-turns "
            "threshold). Use Loca for a while first, then rebuild.",
            file=sys.stderr,
        )
        return 2
    counts = write_split(examples, Path(args.out).expanduser(), seed=args.seed)
    total = sum(counts.values())
    print(f"Wrote {total} examples to {args.out}:")
    for name, count in counts.items():
        print(f"  {name}: {count}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    data_dir = Path(args.data).expanduser()
    if not (data_dir / "train.jsonl").exists():
        print(
            f"No train.jsonl in {data_dir}. Run `build` first.",
            file=sys.stderr,
        )
        return 2
    cmd = [
        sys.executable, "-m", "mlx_lm.lora",
        "--train",
        "--model", str(Path(args.model).expanduser()),
        "--data", str(data_dir),
        "--iters", str(args.iters),
        "--adapter-path", str(Path(args.adapter_out).expanduser()),
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="loca-train",
        description="Foundation CLI for fine-tuning local models on Loca conversations via MLX LoRA.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build JSONL dataset from stored conversations")
    b.add_argument("--out", required=True, help="Output directory for train/valid/test JSONL")
    b.add_argument("--min-turns", type=int, default=2,
                   help="Drop conversations shorter than this many user/assistant turns")
    b.add_argument("--seed", type=int, default=42)
    b.set_defaults(func=cmd_build)

    t = sub.add_parser("train", help="Run MLX LoRA training on a built dataset")
    t.add_argument("--model", required=True, help="Path to a local MLX model directory")
    t.add_argument("--data", required=True, help="Dataset directory (from `build`)")
    t.add_argument("--iters", type=int, default=1000, help="Training iterations")
    t.add_argument("--adapter-out", default="./loca-adapter",
                   help="Where to save the LoRA adapter weights")
    t.set_defaults(func=cmd_train)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
