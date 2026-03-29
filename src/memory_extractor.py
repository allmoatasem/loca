"""
Memory extraction — three passes over a conversation to extract:

  1. user_fact     — durable facts about the user (preferences, projects, context)
  2. knowledge     — facts verified via tool calls (web_search, web_fetch results)
  3. correction    — rules the user has taught the model (corrections, guidance)

Each pass is a separate LLM call returning a JSON array of strings.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

MemoryType = Literal["user_fact", "knowledge", "correction"]

# ── System prompts for each pass ──────────────────────────────────────────────

_USER_FACT_SYSTEM = """\
You are a memory-extraction assistant.
Read the conversation and extract concise, durable facts about the USER — \
their preferences, expertise, projects they're working on, personal context, \
or explicit statements they made about themselves.

Rules:
- Focus only on the user, not the assistant's replies.
- Each fact must be self-contained (no pronouns that need context).
- Ignore small-talk or one-off requests.
- If nothing notable, return [].

Return ONLY a JSON array of short strings, no explanation.
Example: ["Prefers Python over JS", "Building a local AI app on macOS called Loca", "Uses M3 Ultra Mac Studio with 96 GB RAM"]\
"""

_KNOWLEDGE_SYSTEM = """\
You are a memory-extraction assistant.
Read the conversation and extract factual claims that were VERIFIED by the assistant \
using a tool (web_search, web_fetch, file_read, shell_exec).

Rules:
- Only extract facts that appeared as a result of a tool call — not the assistant's training knowledge.
- Each fact must be specific, self-contained, and dateable (include the date if mentioned).
- Format: "FACT — source: web_search/web_fetch/etc, date if known"
- If no tool results are in the conversation, return [].

Return ONLY a JSON array of short strings, no explanation.
Example: ["2026 Oscar Best Picture: 'One Battle After Another' (Paul Thomas Anderson) — source: web_search", \
"UK inflation rate March 2026: 3.1% — source: web_fetch"]\
"""

_CORRECTION_SYSTEM = """\
You are a memory-extraction assistant.
Read the conversation and extract CORRECTIONS or GUIDANCE the user gave to the assistant — \
moments where the user told the assistant it was wrong, should behave differently, \
or should remember a rule going forward.

Rules:
- Capture the rule, not the specific instance (generalise from "you were wrong about X" to "trust tool results over training").
- Only extract genuine corrections, not follow-up questions or clarifications.
- Each correction must be actionable and self-contained.
- If no corrections occurred, return [].

Return ONLY a JSON array of short strings, no explanation.
Example: ["Trust web_search results as ground truth even if they contradict training data cutoff", \
"Do not apologize for correct information retrieved via tools"]\
"""


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _format_conversation(messages: list[dict], max_per_msg: int = 1000) -> str:
    return "\n".join(
        f"{m['role'].upper()}: {str(m.get('content', ''))[:max_per_msg]}"
        for m in messages
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    )


def _has_tool_results(messages: list[dict]) -> bool:
    """Check whether any message contains a tool_result tag — skip knowledge pass if not."""
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str) and "<tool_result" in content:
            return True
    return False


def _has_user_correction(messages: list[dict]) -> bool:
    """Cheap heuristic — only run correction pass if user messages have correction signals."""
    correction_signals = {"wrong", "no,", "no.", "incorrect", "you should", "stop", "learn from", "remember"}
    for m in messages:
        if m.get("role") == "user":
            text = str(m.get("content", "")).lower()
            if any(sig in text for sig in correction_signals):
                return True
    return False


async def _extract_pass(
    messages: list[dict],
    system_prompt: str,
    model: str,
    api_base: str,
) -> list[str]:
    """Run one extraction pass and return a list of strings."""
    recent = [m for m in messages[-12:] if m.get("role") in ("user", "assistant")]
    if not recent:
        return []

    conv_text = _format_conversation(recent)

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                f"{api_base.rstrip('/')}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": conv_text},
                    ],
                    "stream": False,
                    "temperature": 0.1,
                    "max_tokens": 400,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"] or ""
            match = re.search(r"\[.*?\]", raw, re.DOTALL)
            if match:
                facts = json.loads(match.group())
                return [f for f in facts if isinstance(f, str) and f.strip()]
    except Exception as exc:
        logger.warning("Memory extraction pass skipped: %s", exc)
    return []


# ── Public API ────────────────────────────────────────────────────────────────

async def extract_memories(
    messages: list[dict],
    model: str,
    api_base: str,
) -> dict[str, list[str]]:
    """
    Run up to three extraction passes over the conversation.
    Returns a dict: {memory_type: [fact, ...]}

    Skips knowledge pass if no tool results are present.
    Skips correction pass if no correction signals are present.
    """
    results: dict[str, list[str]] = {
        "user_fact": [],
        "knowledge": [],
        "correction": [],
    }

    # Pass 1: always run
    results["user_fact"] = await _extract_pass(messages, _USER_FACT_SYSTEM, model, api_base)

    # Pass 2: only if tool results exist in the conversation
    if _has_tool_results(messages):
        results["knowledge"] = await _extract_pass(messages, _KNOWLEDGE_SYSTEM, model, api_base)

    # Pass 3: only if correction signals exist
    if _has_user_correction(messages):
        results["correction"] = await _extract_pass(messages, _CORRECTION_SYSTEM, model, api_base)

    return results
