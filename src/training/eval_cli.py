"""Evaluation CLI — compare base model vs base+adapter on a prompt set.

Scope: deliberately minimal. No benchmark frameworks, no metric models,
no human-rating pipeline. Hits Loca's `/v1/chat/completions` once per
prompt per configuration (base / adapter), writes a markdown doc with
side-by-side responses and per-turn latency/token numbers. The user
rates by eye.

The prompt set in `default_eval_prompts.jsonl` is opinionated: 20 items
split across style, tone, structure, reasoning, factual recall, code,
and critical thinking. Override with `--prompts path/to/custom.jsonl`
when the defaults don't match what you're training for.

Usage:

  python -m src.training.eval_cli run \\
      --base Qwen3.6-35B-A3B-6bit \\
      --adapter loca-v1 \\
      --out ./eval-report.md

  python -m src.training.eval_cli run \\
      --base Qwen3.6-35B-A3B-6bit \\
      --adapter loca-v1 \\
      --prompts ./my-eval.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

DEFAULT_PROMPTS = Path(__file__).parent / "default_eval_prompts.jsonl"
DEFAULT_PROXY = "http://127.0.0.1:8000"


def _load_prompts(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"bad jsonl line in {path}: {exc}") from exc
    return rows


def _activate(proxy: str, model: str, adapter: str | None, timeout: float) -> None:
    """Kill + relaunch mlx_lm.server via the Loca proxy. Raises on failure
    so the caller can surface a clear message before we waste eval time."""
    resp = httpx.post(
        f"{proxy}/api/adapters/activate",
        json={"model": model, "adapter": adapter},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise SystemExit(f"activate(model={model!r}, adapter={adapter!r}) → {resp.status_code}: {resp.text}")


def _chat(proxy: str, model: str, prompt: str, timeout: float) -> dict:
    """Single non-streamed completion against the Loca proxy. Returns
    response text + simple timing info (ttft is approximated by the time
    to first byte of the non-stream response, which is close enough for
    eyeballing side-by-side responses)."""
    t0 = time.time()
    resp = httpx.post(
        f"{proxy}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.7,
        },
        timeout=timeout,
    )
    elapsed_ms = (time.time() - t0) * 1000
    if resp.status_code != 200:
        return {
            "text": f"[ERROR: HTTP {resp.status_code}: {resp.text[:200]}]",
            "elapsed_ms": elapsed_ms,
            "completion_tokens": 0,
            "prompt_tokens": 0,
        }
    data = resp.json()
    choice = (data.get("choices") or [{}])[0].get("message", {})
    usage = data.get("usage", {}) or {}
    return {
        "text": (choice.get("content") or "").strip(),
        "elapsed_ms": elapsed_ms,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
    }


def _render_markdown(
    model: str, adapter: str, prompts: list[dict],
    base_results: list[dict], adapter_results: list[dict],
) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append(f"# Eval: `{model}` base vs `{adapter}` adapter")
    lines.append("")
    lines.append(f"- **Generated:** {now}")
    lines.append(f"- **Prompts:** {len(prompts)}")
    lines.append("")
    # Summary table first — helps scan quickly before the long-form diff.
    lines.append("## Summary")
    lines.append("")
    lines.append("| # | Tag | Base ms | Adapter ms | Base toks | Adapter toks |")
    lines.append("|---|-----|---------|------------|-----------|--------------|")
    for i, p in enumerate(prompts):
        b = base_results[i]
        a = adapter_results[i]
        lines.append(
            f"| {p.get('id', i + 1)} | {p.get('tag', '-')} "
            f"| {b['elapsed_ms']:.0f} | {a['elapsed_ms']:.0f} "
            f"| {b['completion_tokens']} | {a['completion_tokens']} |",
        )
    lines.append("")
    # Side-by-side responses.
    for i, p in enumerate(prompts):
        lines.append(f"## {p.get('id', i + 1)} · {p.get('tag', '-')}")
        lines.append("")
        lines.append(f"**Prompt:** {p['prompt']}")
        lines.append("")
        lines.append("### Base")
        lines.append("")
        lines.append(base_results[i]["text"] or "_(empty)_")
        lines.append("")
        lines.append(f"### Adapter `{adapter}`")
        lines.append("")
        lines.append(adapter_results[i]["text"] or "_(empty)_")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    prompts_path = Path(args.prompts).expanduser() if args.prompts else DEFAULT_PROMPTS
    if not prompts_path.exists():
        print(f"prompts file not found: {prompts_path}", file=sys.stderr)
        return 2
    prompts = _load_prompts(prompts_path)
    if not prompts:
        print("no prompts loaded", file=sys.stderr)
        return 2

    proxy = args.proxy.rstrip("/")

    # Baseline pass first — we leave the final server state on the
    # adapter so the app picks up where we left it, and this order
    # means exactly one activate (adapter) and one deactivate (base)
    # regardless of prompt count.
    print(f"Running {len(prompts)} prompts × 2 configs against {proxy}…", file=sys.stderr)
    print("→ switching to base (no adapter)…", file=sys.stderr)
    _activate(proxy, args.base, None, timeout=args.activate_timeout)
    base_results: list[dict] = []
    for i, p in enumerate(prompts):
        print(f"  [base {i + 1}/{len(prompts)}] {p.get('id', i)}", file=sys.stderr)
        base_results.append(_chat(proxy, args.base, p["prompt"], timeout=args.chat_timeout))

    print(f"→ activating adapter {args.adapter!r}…", file=sys.stderr)
    _activate(proxy, args.base, args.adapter, timeout=args.activate_timeout)
    adapter_results: list[dict] = []
    for i, p in enumerate(prompts):
        print(f"  [adapter {i + 1}/{len(prompts)}] {p.get('id', i)}", file=sys.stderr)
        adapter_results.append(_chat(proxy, args.base, p["prompt"], timeout=args.chat_timeout))

    md = _render_markdown(args.base, args.adapter, prompts, base_results, adapter_results)

    out_path = Path(args.out).expanduser() if args.out else (
        Path.home()
        / "Library" / "Application Support" / "Loca" / "data" / "evals"
        / f"{time.strftime('%Y%m%dT%H%M%S')}-{args.base.replace('/', '_')}-{args.adapter}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="loca-eval",
        description="Evaluate a trained LoRA adapter against its base model on a prompt set.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run base vs adapter on prompts, write a markdown report")
    r.add_argument("--base", required=True,
                   help="Base model name (e.g. Qwen3.6-35B-A3B-6bit — matches /api/local-models).")
    r.add_argument("--adapter", required=True,
                   help="Adapter directory name under {base}/adapters/.")
    r.add_argument("--prompts", default=None,
                   help="Optional JSONL prompt set; defaults to the bundled 20-prompt set.")
    r.add_argument("--out", default=None,
                   help="Output markdown path; defaults to ~/Library/Application Support/Loca/data/evals/.")
    r.add_argument("--proxy", default=DEFAULT_PROXY,
                   help=f"Loca proxy URL (default: {DEFAULT_PROXY}).")
    r.add_argument("--chat-timeout", type=float, default=120.0,
                   help="Per-chat HTTP timeout in seconds (default: 120).")
    r.add_argument("--activate-timeout", type=float, default=60.0,
                   help="Per-activate HTTP timeout in seconds (default: 60).")
    r.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
