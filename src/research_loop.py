"""Autonomous research loop — in-process multi-role agent turn.

User asks a question with `autonomous_loop: true` in the request body;
instead of a direct model call, the orchestrator delegates here for a
three-role pipeline:

    Researcher   — plans 2-3 sub-queries, runs them through web_search,
                   merges results with the turn's memory recall.
    Writer       — synthesises the final answer with `[memory: N]`
                   citations that span both memory + web sources.
    Verifier     — post-stream phantom-citation check, reusing PR #72.

v1 scope is deliberately narrow: no Reviewer role (ranking collapses
into Researcher's aggregation), no token-level streaming of the Writer
output (single chunk to keep the orchestrator plumbing boring), no
tool-use for sub-agents (they call the loaded model via `_chat` only).
The follow-up PR can pull any of these up if real usage demands them.

Plan state is checkpointed to `{LOCA_DATA_DIR}/plans/<conv_id>.md` at
each phase boundary. Fire-and-forget — never blocks the loop, matches
the provenance-sidecar pattern. Useful for "what did the agent do on
turn N" forensics; also the intended diff target for watches runner
comparisons in a future PR.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from .provenance import verify_citations
from .tools.web_search import SearchResult

logger = logging.getLogger(__name__)

# Keep these modest — each sub-query is a full web round-trip, and the
# Researcher-generated queries are often overlapping. 3 is enough for
# breadth without exploding the Writer's prompt.
MAX_SUB_QUERIES = 3
MAX_RESULTS_PER_QUERY = 4
# The Researcher's planning call is short — lower temperature keeps it
# from inventing tangential sub-queries. The Writer uses the caller's
# temperature so the user's tuning still applies to the final answer.
RESEARCHER_TEMPERATURE = 0.3


# Callable shape the loop expects from the orchestrator for model calls.
# Keeping it a callable (not a method) so the tests can swap in a stub
# without spinning up an InferenceBackend.
ChatFn = Callable[..., Awaitable[dict]]
SearchFn = Callable[..., Awaitable[list[SearchResult]]]


@dataclass
class LoopSource:
    """Merged source item — either a recalled memory or a web hit. Both
    go into the Writer's `[memory: N]` pool so the existing verifier
    function doesn't need special-casing for mixed origins."""
    idx: int                      # 1-based position in the merged pool
    origin: str                   # "memory" | "web"
    title: str
    snippet: str
    url: str | None = None

    def format_for_prompt(self) -> str:
        head = f"[memory: {self.idx}] ({self.origin})"
        if self.url:
            head += f" {self.url}"
        if self.title:
            head += f" — {self.title}"
        body = self.snippet.strip()
        return f"{head}\n{body}"


@dataclass
class LoopPlan:
    """In-memory state machine. Serialised to markdown on each update so
    an interrupted loop leaves a human-readable trail."""
    conv_id: str
    user_query: str
    started_at: float = field(default_factory=time.time)
    sub_queries: list[str] = field(default_factory=list)
    source_count_memory: int = 0
    source_count_web: int = 0
    phase: str = "started"        # researcher | writer | verifier | done | error
    error: str | None = None
    completed_at: float | None = None
    phantom_citations: list[int] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# Research loop — {self.conv_id}",
            "",
            f"- **Query:** {self.user_query}",
            f"- **Started:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started_at))}",
            f"- **Phase:** {self.phase}",
        ]
        if self.sub_queries:
            lines.append("- **Sub-queries planned:**")
            for q in self.sub_queries:
                lines.append(f"  - {q}")
        lines.append(f"- **Sources:** {self.source_count_memory} memory, {self.source_count_web} web")
        if self.phantom_citations:
            lines.append(f"- **Phantom citations:** {self.phantom_citations}")
        if self.completed_at:
            dur = self.completed_at - self.started_at
            lines.append(f"- **Completed:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.completed_at))} ({dur:.1f}s)")
        if self.error:
            lines.append(f"- **Error:** `{self.error}`")
        return "\n".join(lines) + "\n"


def _plans_dir() -> Path:
    """Resolve the user data dir the same way provenance does — env
    override first, then platform default. Kept local to avoid cross-
    importing a private helper from provenance.py."""
    import os  # noqa: PLC0415
    env = os.environ.get("LOCA_DATA_DIR")
    if env:
        base = Path(env)
    elif os.name == "posix":
        base = Path.home() / "Library" / "Application Support" / "Loca" / "data"
    else:
        base = Path.home() / ".loca" / "data"
    d = base / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_plan(plan: LoopPlan) -> None:
    """Best-effort checkpoint write. Any failure is swallowed — the loop
    must not be held up by a disk hiccup."""
    try:
        (_plans_dir() / f"{plan.conv_id}.md").write_text(plan.to_markdown())
    except OSError as exc:
        logger.debug("plan checkpoint failed for %s: %s", plan.conv_id, exc)


# ---------------------------------------------------------------------
# Researcher — plan sub-queries, run them, merge with recalled memory
# ---------------------------------------------------------------------

_SUB_QUERY_PROMPT = """\
You are a research planner. Given a user question, return a JSON list \
of {n} short web-search sub-queries that together cover the question \
from complementary angles. No prose. No preamble. No numbering. Just a \
JSON array of strings.

User question: {q}

Respond with ONLY the JSON array."""


async def _plan_sub_queries(
    chat_fn: ChatFn,
    user_query: str,
    *,
    n: int = MAX_SUB_QUERIES,
) -> list[str]:
    """Ask the loaded model for n sub-queries. Best-effort parsing; if
    the model wanders, extract anything that looks like a bullet or
    quoted string so we don't fail the whole loop on a malformed
    response."""
    prompt = _SUB_QUERY_PROMPT.format(q=user_query, n=n)
    resp = await chat_fn(
        messages=[{"role": "user", "content": prompt}],
        temperature=RESEARCHER_TEMPERATURE,
        max_tokens=400,
    )
    text = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    queries = _parse_sub_queries(text, fallback=user_query, n=n)
    # Always include the original query as a safety net — if planning
    # produced nonsense, we still have one useful search to run.
    if user_query not in queries:
        queries.insert(0, user_query)
    return queries[:n]


def _parse_sub_queries(text: str, *, fallback: str, n: int) -> list[str]:
    # Primary path: JSON array of strings.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            out = [str(x).strip() for x in parsed if str(x).strip()]
            if out:
                return out[:n]
    except (ValueError, json.JSONDecodeError):
        pass
    # Fallback: strip markdown code fences + retry.
    stripped = re.sub(r"```[a-zA-Z]*\s*|```", "", text).strip()
    if stripped != text:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()][:n]
        except (ValueError, json.JSONDecodeError):
            pass
    # Last resort: split on newlines / bullets / quotes.
    lines = re.split(r"\r?\n|^[\-\*\d\.\)]\s+", text, flags=re.MULTILINE)
    out = [line.strip(' "\'-*·•\t') for line in lines if line.strip()]
    if out:
        return out[:n]
    return [fallback]


async def _run_researcher(
    chat_fn: ChatFn,
    search_fn: SearchFn,
    user_query: str,
    memory_sources: list[LoopSource],
    *,
    max_results_per_query: int = MAX_RESULTS_PER_QUERY,
) -> tuple[list[LoopSource], list[str], list[str]]:
    """Plan + execute. Returns (merged_sources, sub_queries, progress_lines).

    `progress_lines` is what the <think> block shows the user — keeps the
    Researcher's work legible without shipping a dedicated status
    channel."""
    progress: list[str] = []
    sub_queries = await _plan_sub_queries(chat_fn, user_query)
    progress.append(f"Planning: {len(sub_queries)} sub-queries")
    for q in sub_queries:
        progress.append(f"  • {q}")

    web_sources: list[LoopSource] = []
    seen_urls: set[str] = set()
    # Run sub-queries in parallel — they're independent round-trips, and
    # serialising them would add noticeable latency on top of the
    # already-paid Researcher planning call.
    searches = [search_fn(query=q, max_results=max_results_per_query) for q in sub_queries]
    try:
        results_per_query: list[list[SearchResult]] = await asyncio.gather(
            *searches, return_exceptions=False,
        )
    except Exception as exc:
        progress.append(f"  (web search failed: {exc})")
        results_per_query = [[] for _ in sub_queries]

    for q, hits in zip(sub_queries, results_per_query, strict=False):
        kept = 0
        for hit in hits:
            if not hit.url or hit.url in seen_urls:
                continue
            seen_urls.add(hit.url)
            web_sources.append(LoopSource(
                idx=0,  # filled in during merge
                origin="web",
                title=(hit.title or hit.url).strip(),
                snippet=(hit.snippet or hit.content or "")[:600],
                url=hit.url,
            ))
            kept += 1
        progress.append(f"  → {q!r}: {kept} result{'s' if kept != 1 else ''}")

    # Merge: memory first (preserves the caller's rerank-order bias so
    # higher-scoring memories get lower `[memory: N]` indices), then web.
    merged: list[LoopSource] = []
    for i, m in enumerate(memory_sources, start=1):
        merged.append(LoopSource(
            idx=i, origin="memory",
            title=m.title, snippet=m.snippet, url=m.url,
        ))
    for s in web_sources:
        s.idx = len(merged) + 1
        merged.append(s)
    progress.append(f"Pool: {len(memory_sources)} memory + {len(web_sources)} web = {len(merged)} sources")
    return merged, sub_queries, progress


# ---------------------------------------------------------------------
# Reviewer — drop low-signal sources before the Writer sees them
# ---------------------------------------------------------------------

_REVIEWER_PROMPT = """\
You are a research reviewer. Below are {n} numbered sources gathered \
for a user question. Identify the ones that are clearly off-topic, \
irrelevant, or duplicative — those should be dropped before writing \
the answer.

Reply with ONLY a JSON array of source numbers (1-indexed) to drop. \
If every source is useful, reply `[]`. Do not include any prose.

Question: {q}

Sources:
{pool}"""

# Minimum number of sources to keep regardless of the Reviewer's
# verdict. Without this floor a noisy reviewer can starve the Writer
# and cause "no sources" answers when there were actually useful ones
# below the model's confidence threshold.
_MIN_SOURCES_AFTER_REVIEW = 2

# Reviewer is a short JSON-emitting call — same cap as the Researcher's
# planner so we don't budget an entire generation worth of tokens.
_REVIEWER_TEMPERATURE = 0.1
_REVIEWER_MAX_TOKENS  = 200


async def _run_reviewer(
    chat_fn: ChatFn,
    user_query: str,
    sources: list[LoopSource],
) -> tuple[list[LoopSource], list[int]]:
    """Score the source pool and drop the ones the model flags as
    irrelevant. Returns (kept_sources, dropped_indices).

    Best-effort: any parsing failure leaves the pool untouched so a
    misbehaving model can't silently delete the Writer's evidence.
    Indices are 1-based, matching the `[memory: N]` convention the
    Writer prompt uses elsewhere."""
    if len(sources) <= _MIN_SOURCES_AFTER_REVIEW:
        return sources, []

    pool = "\n\n".join(s.format_for_prompt() for s in sources)
    prompt = _REVIEWER_PROMPT.format(n=len(sources), q=user_query, pool=pool)
    try:
        resp = await chat_fn(
            messages=[{"role": "user", "content": prompt}],
            temperature=_REVIEWER_TEMPERATURE,
            max_tokens=_REVIEWER_MAX_TOKENS,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("reviewer call failed: %s", exc)
        return sources, []

    text = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    drop = _parse_drop_indices(text, max_idx=len(sources))
    if not drop:
        return sources, []

    # Honour the floor — never let the Reviewer drop the pool below the
    # minimum, even if it flags every source. Walk drop in descending
    # confidence order (the model's reply order) so the last-resort
    # survivors are the highest-ranked originals.
    keep_min = max(_MIN_SOURCES_AFTER_REVIEW, len(sources) - len(drop))
    drop_set = set(drop)
    kept: list[LoopSource] = []
    for s in sources:
        if s.idx in drop_set and len(sources) - len(drop_set) < keep_min:
            # Putting this back into the keep set restores the floor.
            drop_set.discard(s.idx)
        if s.idx not in drop_set:
            kept.append(s)
    # Reindex the kept sources so `[memory: N]` stays contiguous —
    # otherwise the Writer can cite an integer that no longer exists.
    actually_dropped: list[int] = []
    for new_idx, s in enumerate(kept, start=1):
        if s.idx != new_idx:
            actually_dropped.extend(
                i for i in drop if i not in actually_dropped
            )
            s.idx = new_idx
    return kept, sorted(set(drop) - {s.idx for s in kept})


def _parse_drop_indices(text: str, *, max_idx: int) -> list[int]:
    """Parse the Reviewer's JSON array. Tolerates leading prose, code
    fences, and stray whitespace. Returns 1-based indices clamped to
    the valid range."""
    candidates: list[int] = []
    # Strip code fences first.
    cleaned = re.sub(r"```[a-zA-Z]*\s*|```", "", text).strip()
    # Find the first JSON array in the response.
    m = re.search(r"\[[^\]]*\]", cleaned)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    for x in parsed:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= max_idx:
            candidates.append(n)
    # Dedupe while preserving order.
    seen: set[int] = set()
    out: list[int] = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ---------------------------------------------------------------------
# Writer — synthesise with [memory: N] citations
# ---------------------------------------------------------------------

_WRITER_SYSTEM = """\
You are answering a user's question using a numbered pool of sources. \
Cite every concrete claim with `[memory: N]` where N is the source \
index. Never invent a source number outside the pool. If the pool is \
empty or the sources don't support the question, say so plainly \
instead of filling with generic knowledge.

Tone: direct, specific, grounded. No preamble. No meta-commentary \
about research process or sources unless the user asked for it."""


async def _run_writer(
    chat_fn: ChatFn,
    history: list[dict],
    user_query: str,
    sources: list[LoopSource],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    pool = "\n\n".join(s.format_for_prompt() for s in sources) if sources else "(no sources)"
    messages: list[dict] = [{"role": "system", "content": _WRITER_SYSTEM}]
    # Preserve prior turns so follow-ups stay coherent.
    messages.extend(history)
    messages.append({
        "role": "user",
        "content": (
            f"<sources>\n{pool}\n</sources>\n\n"
            f"Question: {user_query}"
        ),
    })
    kwargs: dict[str, Any] = {"messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    resp = await chat_fn(**kwargs)
    return (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


# ---------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------

async def run_research_loop(
    *,
    chat_fn: ChatFn,
    search_fn: SearchFn,
    user_query: str,
    history: list[dict],
    memory_sources: list[LoopSource],
    conv_id: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """The async generator the orchestrator delegates to on
    `autonomous_loop=True`. Yields one `<think>...</think>` progress
    block, then the Writer's final answer, then (if any) a verifier
    footer. Always writes a plan checkpoint regardless of outcome.

    The orchestrator wraps the yielded strings into SSE chunks the same
    way it does for a normal turn — this function doesn't talk SSE.
    """
    plan = LoopPlan(conv_id=conv_id, user_query=user_query)
    _write_plan(plan)

    progress_lines: list[str]
    try:
        plan.phase = "researcher"
        _write_plan(plan)
        sources, sub_queries, progress_lines = await _run_researcher(
            chat_fn, search_fn, user_query, memory_sources,
        )
        plan.sub_queries = sub_queries
        plan.source_count_memory = sum(1 for s in sources if s.origin == "memory")
        plan.source_count_web = sum(1 for s in sources if s.origin == "web")
        _write_plan(plan)
    except Exception as exc:
        plan.phase = "error"
        plan.error = f"researcher: {exc}"
        _write_plan(plan)
        logger.exception("research loop: researcher failed")
        yield f"<think>\nResearch loop failed during planning: {exc}\n</think>\n"
        yield f"I couldn't gather sources for this question — {exc}."
        return

    # Reviewer — drop low-signal sources before Writer wastes context
    # on them. Cheap call, runs after Researcher so the Writer sees a
    # tighter pool. Failures fall through silently (returns the
    # original sources untouched).
    try:
        plan.phase = "reviewer"
        _write_plan(plan)
        sources, dropped = await _run_reviewer(chat_fn, user_query, sources)
        if dropped:
            progress_lines.append(
                f"Reviewer: dropped {len(dropped)} low-signal source"
                f"{'s' if len(dropped) != 1 else ''}"
            )
        else:
            progress_lines.append("Reviewer: kept all sources")
        plan.source_count_memory = sum(1 for s in sources if s.origin == "memory")
        plan.source_count_web    = sum(1 for s in sources if s.origin == "web")
        _write_plan(plan)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("reviewer step failed, continuing with full pool: %s", exc)
        progress_lines.append(f"Reviewer: skipped ({exc})")

    # Emit the think block first so the UI renders "research trail" up
    # top and the answer below. A single yield keeps the markdown parser
    # happy; the chat view's ThinkBlock component collapses it.
    think_body = "\n".join(["Autonomous research loop:", *progress_lines, "Writing answer…"])
    yield f"<think>\n{think_body}\n</think>\n\n"

    try:
        plan.phase = "writer"
        _write_plan(plan)
        answer = await _run_writer(
            chat_fn, history, user_query, sources,
            temperature=temperature, max_tokens=max_tokens,
        )
    except Exception as exc:
        plan.phase = "error"
        plan.error = f"writer: {exc}"
        _write_plan(plan)
        logger.exception("research loop: writer failed")
        yield f"I gathered {len(sources)} sources but couldn't synthesise an answer: {exc}"
        return

    yield answer

    # Verifier runs post-answer — cheap (regex + membership check), so
    # we always run it even when the Writer's output looks clean.
    try:
        plan.phase = "verifier"
        phantoms = verify_citations(answer, retrieved_count=len(sources))
        plan.phantom_citations = phantoms
        if phantoms:
            joined = ", ".join(str(n) for n in phantoms)
            yield (
                f"\n\n> ⚠ Citation check: the answer referenced [memory: {joined}] "
                f"which {'is' if len(phantoms) == 1 else 'are'} outside the "
                f"{len(sources)}-source pool. Treat those claims with scepticism."
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("verifier step failed: %s", exc)

    plan.phase = "done"
    plan.completed_at = time.time()
    _write_plan(plan)
