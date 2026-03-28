"""
Memory extraction: ask the local model to pull out durable facts about the
user from a conversation, then persist them via store.add_memory().
"""
from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_SYSTEM = """\
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


async def extract_memories(messages: list[dict], model: str, api_base: str) -> list[str]:
    """Return a (possibly empty) list of memorable facts extracted from the conversation."""
    recent = [m for m in messages[-10:] if m.get("role") in ("user", "assistant")]
    if not recent:
        return []

    conv_text = "\n".join(
        f"{m['role'].upper()}: {str(m.get('content', ''))[:800]}"
        for m in recent
        if isinstance(m.get("content"), str)
    )

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                f"{api_base.rstrip('/')}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
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
        logger.warning("Memory extraction skipped: %s", exc)
    return []
