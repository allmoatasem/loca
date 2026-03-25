"""
Router — inspects each user message and decides which model to use.
Routing is keyword + heuristic based (no LLM call) to keep latency low.

Priority (first match wins):
  1. Image attached             → general
  2. Explicit /command override → that model
  3. High-complexity code task  → code
  4. Reasoning/planning task    → reason
  5. Creative/writing task      → write
  6. Default                    → general
"""

import re
from dataclasses import dataclass
from enum import Enum


class Model(str, Enum):
    GENERAL = "general"
    REASON = "reason"
    CODE = "code"
    WRITE = "write"


@dataclass
class RouteResult:
    model: Model
    reason: str
    search_triggered: bool = False
    search_query: str | None = None
    override_command: str | None = None  # original slash command if present


# ---------------------------------------------------------------------------
# Keyword banks
# ---------------------------------------------------------------------------

_CODE_KEYWORDS = [
    r"\brefactor\b", r"\barchitecture\b", r"\bdebug\b", r"\bdebugging\b",
    r"\boptimize\b", r"\bperformance\b", r"\bmigrat\b", r"\bimport\b",
    r"\bmodule\b", r"\bpackage\b", r"\brepository\b", r"\bcodebase\b",
    r"\bpull request\b", r"\bpr\b", r"\bcommit\b", r"\bclass\b",
    r"\bfunction\b", r"\bmethod\b", r"\bapi\b", r"\binterface\b",
    r"\btype hint\b", r"\bunit test\b", r"\bintegration test\b",
]

_CODE_COMPLEXITY_SIGNALS = [
    r"multi.?file", r"multiple files?", r"across (the )?codebase",
    r"large (codebase|project|repo)", r"entire (project|repo|codebase)",
    r"architecture (review|decision|design)", r"\brefactor\b",
    r"(?:\d{3,}|hundred|thousand)\s+lines?",
]

_REASON_KEYWORDS = [
    r"\bthink through\b", r"\breason about\b", r"\bplan\b", r"\bplanning\b",
    r"\bcompare\b", r"\btrade.?off\b", r"\bbest approach\b",
    r"\bpros and cons\b", r"\bwhat('s| is) (the )?best\b",
    r"\bshould i\b", r"\bwhich (is|would|should)\b",
    r"\bstep.?by.?step\b", r"\bwalk me through\b",
    r"\bmath\b", r"\bequation\b", r"\bprove\b", r"\bproof\b",
    r"\bsolve\b", r"\bpuzzle\b", r"\blogic\b", r"\bdeduction\b",
    r"\banalyze\b", r"\banalysis\b", r"\bbreakdown\b",
]

_WRITE_KEYWORDS = [
    r"\bwrite (a|an|me|the)\b", r"\bdraft\b", r"\bcompose\b",
    r"\bcover letter\b", r"\bresume\b", r"\bemail\b", r"\bblog post\b",
    r"\barticle\b", r"\bessay\b", r"\bstory\b", r"\bpoem\b", r"\blyrics\b",
    r"\bsummariz", r"\bsummary\b", r"\bparaphrase\b", r"\brewrite\b",
    r"\bimprove (my |the )?(writing|text|prose|copy)\b",
    r"\bmake (this|it) (more |)(professional|formal|casual|concise|engaging)\b",
    r"\bedit (this|my)\b", r"\bproofread\b",
    r"\bpress release\b", r"\bproposal\b", r"\breport\b", r"\bdocumentation\b",
    r"\breadme\b", r"\bcreative\b",
]

_SEARCH_KEYWORDS = [
    r"\bcurrent(ly)?\b", r"\blatest\b", r"\brecent(ly)?\b",
    r"\btoday\b", r"\bright now\b", r"\bthis (year|month|week)\b",
    r"\bnews\b", r"\bprice\b", r"\bstock\b",
    r"\bwho is (the )?(ceo|founder|president|head)\b",
    r"\bwhat (is|are) the (latest|current|new)\b",
    r"\blook up\b", r"\bsearch for\b", r"\bfind information\b",
    r"\bversion \d", r"\brelease(d)?\b", r"\bannounced?\b",
    r"\bbreaking\b",
]

_IMAGE_EXTENSIONS = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|bmp|svg|tiff?)$", re.IGNORECASE
)


def _any_match(text: str, patterns: list[str]) -> bool:
    """Return True if any pattern matches anywhere in text (case-insensitive)."""
    lowered = text.lower()
    return any(re.search(p, lowered) for p in patterns)


def _has_code_block(text: str) -> bool:
    return bool(re.search(r"```[\s\S]*?```|`[^`]+`", text))


def _has_file_path(text: str) -> bool:
    return bool(re.search(r"[\w/\\.-]+\.[a-zA-Z]{1,6}(?:\s|$|:)", text))


def _estimate_complexity(text: str) -> bool:
    """True if the message signals a high-complexity code task."""
    return _any_match(text, _CODE_COMPLEXITY_SIGNALS)


# ---------------------------------------------------------------------------
# Manual override parsing
# ---------------------------------------------------------------------------

_OVERRIDE_MAP = {
    "/code": Model.CODE,
    "/reason": Model.REASON,
    "/general": Model.GENERAL,
    "/write": Model.WRITE,
}


def _parse_override(message: str) -> tuple[Model | None, str | None, str]:
    """
    Returns (model_override, command_token, cleaned_message).
    cleaned_message has the slash command stripped.
    """
    stripped = message.strip()
    for cmd, model in _OVERRIDE_MAP.items():
        if stripped.lower().startswith(cmd):
            rest = stripped[len(cmd):].strip()
            return model, cmd, rest
    return None, None, message


def _parse_web_command(message: str) -> tuple[str | None, str]:
    """
    If message starts with /web <query>, return (query, remainder).
    """
    m = re.match(r"^/web\s+(.+)", message.strip(), re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip(), ""
    return None, message


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_MODEL_HINT_MAP = {
    "general": Model.GENERAL,
    "reason": Model.REASON,
    "code": Model.CODE,
    "write": Model.WRITE,
}


def route(
    message: str,
    has_image: bool = False,
    conversation_history: list[dict] | None = None,
    model_hint: str | None = None,
) -> RouteResult:
    """
    Determine which model to use and whether a web search should be triggered.

    Args:
        message:              Raw user message text.
        has_image:            True if the request includes an attached image.
        conversation_history: Prior messages (unused by heuristic router but
                               available for future enhancement).
        model_hint:           Model name from the client request (e.g. Open WebUI
                               model selector). Overrides content-based routing if
                               it matches a known model alias.

    Returns:
        RouteResult with .model, .reason, .search_triggered, .search_query
    """
    # 1. Check for /web command first (may combine with other overrides)
    web_query, message = _parse_web_command(message)
    search_triggered = web_query is not None
    search_query = web_query

    # 1b. Honour explicit model selection from the client (e.g. Open WebUI dropdown)
    if model_hint and model_hint.lower() in _MODEL_HINT_MAP:
        forced_model = _MODEL_HINT_MAP[model_hint.lower()]
        # Still auto-detect search intent for the chosen model
        if not search_triggered and _any_match(message, _SEARCH_KEYWORDS):
            search_triggered = True
            search_query = message
        return RouteResult(
            model=forced_model,
            reason=f"Client selected model: {model_hint}",
            search_triggered=search_triggered,
            search_query=search_query,
        )

    # 2. Image attached → always general
    if has_image:
        return RouteResult(
            model=Model.GENERAL,
            reason="Image detected — routing to vision-capable general model",
            search_triggered=search_triggered,
            search_query=search_query,
        )

    # 3. Explicit model override
    model_override, cmd_token, message_clean = _parse_override(message)
    if model_override is not None:
        # Auto-detect search even on overridden routes
        if not search_triggered and _any_match(message_clean, _SEARCH_KEYWORDS):
            search_triggered = True
            search_query = message_clean
        return RouteResult(
            model=model_override,
            reason=f"Explicit override: {cmd_token}",
            search_triggered=search_triggered,
            search_query=search_query,
            override_command=cmd_token,
        )
    else:
        message_clean = message

    # 4. Auto-detect search intent (runs regardless of model selection)
    if not search_triggered and _any_match(message_clean, _SEARCH_KEYWORDS):
        search_triggered = True
        search_query = message_clean

    # 5. Code task signals — only route to `code` if high complexity
    has_code_signal = (
        _has_code_block(message_clean)
        or _has_file_path(message_clean)
        or _any_match(message_clean, _CODE_KEYWORDS)
    )
    if has_code_signal and _estimate_complexity(message_clean):
        return RouteResult(
            model=Model.CODE,
            reason="High-complexity code task detected",
            search_triggered=search_triggered,
            search_query=search_query,
        )

    # 6. Reasoning task signals
    if _any_match(message_clean, _REASON_KEYWORDS):
        return RouteResult(
            model=Model.REASON,
            reason="Reasoning/planning task detected",
            search_triggered=search_triggered,
            search_query=search_query,
        )

    # 7. Creative/writing task signals
    if _any_match(message_clean, _WRITE_KEYWORDS):
        return RouteResult(
            model=Model.WRITE,
            reason="Creative/writing task detected",
            search_triggered=search_triggered,
            search_query=search_query,
        )

    # 8. Default
    return RouteResult(
        model=Model.GENERAL,
        reason="Default routing",
        search_triggered=search_triggered,
        search_query=search_query,
    )
