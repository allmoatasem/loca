"""Retrieval provenance + citation verification.

Extends PRs #50–54 (recall → multi-query → rerank → `[memory: N]` citations)
with an auditable, per-turn paper trail and a phantom-citation check.

Two products:

1. **Sidecar file per turn** at
   `$LOCA_DATA_DIR/provenance/YYYY-MM-DD/<hh-mm-ss>-<conv_id>.md`
   recording the user query, expanded queries, retrieved memories
   (id + score + rank), which memory indices were cited in the final
   answer, which were retrieved but unused, and any phantom citations
   (IDs the model made up). Enables after-the-fact audit and
   feedback-loop analytics on the retrieval stack.

2. **Verifier pass** — regex-parse `[memory: N]` from the assistant's
   completed response, flag any N outside `[1, len(retrieved)]` as a
   phantom, and append a user-visible footnote paragraph so the user
   can see which citations are trustworthy.

Sidecar writes are best-effort (never block a reply) and silently fall
through on filesystem errors.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Model was instructed to cite with `[memory: N]` (see PR #54's
# format_for_prompt in src/plugins/memory_plugin.py). Match the same shape,
# tolerating whitespace, and ignore other bracketed tokens.
_CITATION_RE = re.compile(r"\[memory:\s*(\d+)\s*\]", re.IGNORECASE)


@dataclass
class RetrievedMemory:
    """A single memory that was surfaced by recall + rerank.

    `index` is the 1-based position as shown to the model in the
    `<memory>` block (matches the `[memory: N]` citation tag). `id` is
    the storage-layer identifier (SQLite rowid for built-in plugin,
    Chroma doc id for MemPalace). `score` is plugin-native — higher is
    more relevant for MemPalace, lower (distance) is closer for
    sqlite-vec; kept raw so analytics can normalise later.
    """
    index: int
    id: str
    score: float
    content: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Provenance:
    """One sidecar's worth of state. Written to disk after the turn
    finishes streaming."""
    user_query: str
    recall_query: str
    expanded_queries: list[str]
    retrieved: list[RetrievedMemory]
    cited: list[int]
    phantoms: list[int]
    conv_id: str | None = None
    skipped_meta_query: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    def to_dict(self) -> dict:
        return {
            "user_query": self.user_query,
            "recall_query": self.recall_query,
            "expanded_queries": list(self.expanded_queries),
            "retrieved": [m.to_dict() for m in self.retrieved],
            "cited": list(self.cited),
            "phantoms": list(self.phantoms),
            "conv_id": self.conv_id,
            "skipped_meta_query": self.skipped_meta_query,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Provenance":
        return cls(
            user_query=data["user_query"],
            recall_query=data["recall_query"],
            expanded_queries=list(data.get("expanded_queries", [])),
            retrieved=[RetrievedMemory(**m) for m in data.get("retrieved", [])],
            cited=list(data.get("cited", [])),
            phantoms=list(data.get("phantoms", [])),
            conv_id=data.get("conv_id"),
            skipped_meta_query=bool(data.get("skipped_meta_query", False)),
            timestamp=data["timestamp"],
        )


# ---------------------------------------------------------------------------
# Citation extraction + verification
# ---------------------------------------------------------------------------

def extract_citations(text: str) -> list[int]:
    """Return the deduped, in-order list of memory indices cited in `text`.

    Only matches the `[memory: N]` format (PR #54's schema). Other
    bracketed contents — `[memory: alpha]`, `[todo: 1]`, footnotes —
    are ignored. The same index repeated within the text appears once.
    """
    seen: set[int] = set()
    out: list[int] = []
    for match in _CITATION_RE.finditer(text):
        try:
            n = int(match.group(1))
        except ValueError:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def verify_citations(text: str, retrieved_count: int) -> list[int]:
    """Return citation indices in `text` that don't correspond to any
    retrieved memory.

    Memory indices run 1..retrieved_count inclusive. Any citation
    outside that range — including `[memory: 0]` and anything beyond
    the last retrieved memory — is a phantom.
    """
    return [n for n in extract_citations(text) if n < 1 or n > retrieved_count]


def append_verifier_footer(text: str, phantoms: Iterable[int]) -> str:
    """Append a plain-paragraph footnote about phantom citations.

    If `phantoms` is empty, returns `text` unchanged. The output is
    intended for post-stream annotation — it lives in the same SSE
    text channel the model already wrote, so the UI renders it inline
    without any new rendering path.
    """
    phantom_list = list(phantoms)
    if not phantom_list:
        return text
    labels = ", ".join(f"[memory: {n}]" for n in phantom_list)
    plural = "citations don't" if len(phantom_list) > 1 else "citation doesn't"
    note = (
        f"\n\n> *Note: {labels} {plural} match any retrieved memory. "
        "This may be a hallucination.*"
    )
    return text + note


# ---------------------------------------------------------------------------
# Sidecar file IO
# ---------------------------------------------------------------------------

def _default_root() -> Path:
    env = os.environ.get("LOCA_DATA_DIR")
    if env:
        base = Path(env)
    elif os.name == "posix":
        base = Path.home() / "Library" / "Application Support" / "Loca" / "data"
    else:
        base = Path.home() / ".loca" / "data"
    return base / "provenance"


def _render_markdown(prov: Provenance) -> str:
    lines: list[str] = []
    lines.append(f"# Provenance · {prov.timestamp}")
    lines.append("")
    if prov.conv_id:
        lines.append(f"**Conversation:** `{prov.conv_id}`")
    lines.append(f"**User query:** {prov.user_query.strip() or '_(empty)_'}")
    if prov.skipped_meta_query:
        lines.append("**Recall:** skipped — meta-query referenced parametric knowledge")
    if prov.expanded_queries and len(prov.expanded_queries) > 1:
        lines.append(f"**Expanded queries:** {len(prov.expanded_queries)} (broad query detected)")
    lines.append("")

    lines.append(f"## Retrieved ({len(prov.retrieved)})")
    if not prov.retrieved:
        lines.append("_None — no memory backend or empty store._")
    else:
        for m in prov.retrieved:
            snippet = m.content.replace("\n", " ").strip()
            if len(snippet) > 160:
                snippet = snippet[:157] + "…"
            marker = "✓ cited" if m.index in prov.cited else "· unused"
            lines.append(f"- `[memory: {m.index}]` (score {m.score:.2f}) {marker} — {snippet}")
    lines.append("")

    if prov.phantoms:
        lines.append("## Phantom citations")
        for n in prov.phantoms:
            lines.append(f"- `[memory: {n}]` — no such memory retrieved (footnoted to user)")
        lines.append("")

    retrieved_indices = {m.index for m in prov.retrieved}
    retrieved_unused = sorted(retrieved_indices - set(prov.cited))
    if retrieved_unused:
        lines.append("## Retrieved but unused")
        lines.append(", ".join(f"`[memory: {n}]`" for n in retrieved_unused))
        lines.append("")

    # Machine-readable tail so analytics can skip the prose.
    lines.append("## Raw")
    lines.append("```json")
    lines.append(json.dumps(prov.to_dict(), indent=2))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _filename_for(prov: Provenance) -> str:
    # Parse the ISO timestamp once so we don't rely on local clock behaviour.
    try:
        ts = datetime.strptime(prov.timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        ts = datetime.now(timezone.utc)
    slug = prov.conv_id or "adhoc"
    return f"{ts.strftime('%H-%M-%S')}-{slug}.md"


def _daily_subdir(prov: Provenance) -> str:
    try:
        ts = datetime.strptime(prov.timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%d")


def write_provenance(prov: Provenance, root: Path | None = None) -> Path:
    """Render `prov` to markdown and write it under `root/YYYY-MM-DD/`.

    Returns the written path. Best-effort: if the filesystem write
    fails, the exception propagates — callers are expected to wrap in
    a try/except (the orchestrator does).
    """
    target_root = Path(root) if root is not None else _default_root()
    target_dir = target_root / _daily_subdir(prov)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / _filename_for(prov)
    path.write_text(_render_markdown(prov), encoding="utf-8")
    return path
