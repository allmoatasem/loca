"""
model_sync.py — auto-detect LM Studio and Ollama models, update config.yaml.

Runs at Loca startup (before the proxy) so new models appear automatically.
Can also be run standalone:
    python src/model_sync.py [/path/to/config.yaml]
"""

import logging
import os
import re
import sys

import httpx
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LM_STUDIO_BASE = os.environ.get("LMSTUDIO_URL", "http://localhost:1234")
OLLAMA_BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# (regex pattern, role) — checked in order; first match wins
ROLE_PATTERNS: list[tuple[str, str]] = [
    (r"coder|codestral|deepseek.coder|qwen.*coder|starcoder|granite.*code", "code"),
    (r"reason|think(?:er|ing)?|nemotron|qwq|deepseek.r1|\br1\b|marco.o1", "reason"),
    (r"distil(?:led)?|opus|writer|creative|story", "write"),
]

_ROLE_DEFAULTS: dict[str, dict] = {
    "general": {
        "always_loaded": True,
        "idle_unload_minutes": None,
        "capabilities": ["vision", "code", "chat", "analysis"],
    },
    "reason": {
        "always_loaded": False,
        "idle_unload_minutes": 10,
        "capabilities": ["reasoning", "planning"],
    },
    "code": {
        "always_loaded": False,
        "idle_unload_minutes": 10,
        "capabilities": ["code"],
    },
    "write": {
        "always_loaded": False,
        "idle_unload_minutes": 10,
        "capabilities": ["creative_writing", "summarization", "drafting"],
    },
}


def _classify(model_id: str) -> str:
    lower = model_id.lower()
    for pattern, role in ROLE_PATTERNS:
        if re.search(pattern, lower):
            return role
    return "general"


def _param_billions(model_id: str) -> float:
    """Extract parameter count (billions) from model name, e.g. '35b' → 35.0."""
    m = re.search(r"(\d+\.?\d*)b", model_id.lower())
    return float(m.group(1)) if m else 0.0


def _fetch_lmstudio() -> list[tuple[str, str]]:
    """Returns [(model_id, api_base)] from LM Studio."""
    try:
        resp = httpx.get(f"{LM_STUDIO_BASE}/v1/models", timeout=5.0)
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("data", [])]
        logger.info(f"LM Studio: {len(ids)} model(s) — {ids}")
        return [(mid, LM_STUDIO_BASE) for mid in ids]
    except Exception as e:
        logger.warning(f"LM Studio unreachable: {e}")
        return []


def _fetch_ollama() -> list[tuple[str, str]]:
    """Returns [(model_id, api_base)] from Ollama if it is running."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=3.0)
        resp.raise_for_status()
        ids = [m["name"] for m in resp.json().get("models", [])]
        if ids:
            logger.info(f"Ollama:     {len(ids)} model(s) — {ids}")
        return [(mid, OLLAMA_BASE) for mid in ids]
    except Exception:
        return []  # Ollama not running — silent


def sync(config_path: str) -> None:
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # LM Studio models first, Ollama second (LM Studio takes priority on duplicates)
    all_candidates: list[tuple[str, str]] = _fetch_lmstudio() + _fetch_ollama()

    if not all_candidates:
        logger.warning("No models found from LM Studio or Ollama — keeping existing config.")
        return

    # De-duplicate by model_id (first occurrence wins → LM Studio preferred)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for mid, base in all_candidates:
        if mid not in seen:
            seen.add(mid)
            unique.append((mid, base))

    # Bucket by role
    by_role: dict[str, list[tuple[str, str]]] = {r: [] for r in _ROLE_DEFAULTS}
    for mid, base in unique:
        role = _classify(mid)
        by_role[role].append((mid, base))

    # For "general", pick the largest unclassified model
    by_role["general"].sort(key=lambda x: _param_billions(x[0]), reverse=True)

    existing: dict = config.get("models", {})
    new_models: dict = {}

    for role, defaults in _ROLE_DEFAULTS.items():
        pool = by_role[role]

        if not pool:
            if role in existing:
                new_models[role] = existing[role]  # keep existing if nothing detected
            continue

        best_id, best_base = pool[0]
        old_entry = existing.get(role, {})

        entry: dict = {
            "lmstudio_name": best_id,
            "always_loaded": old_entry.get("always_loaded", defaults["always_loaded"]),
            "idle_unload_minutes": old_entry.get("idle_unload_minutes", defaults["idle_unload_minutes"]),
            "capabilities": old_entry.get("capabilities", defaults["capabilities"]),
        }
        # Ollama models need a different base URL at inference time
        if best_base != LM_STUDIO_BASE:
            entry["api_base"] = best_base

        prev = old_entry.get("lmstudio_name", "—")
        status = "updated" if best_id != prev else "unchanged"
        logger.info(f"  [{role:7s}] {status}: {best_id}")
        if len(pool) > 1:
            alts = [m for m, _ in pool[1:]]
            logger.info(f"            also available: {alts}")

        new_models[role] = entry

    config["models"] = new_models

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info(f"config.yaml written — {len(new_models)} role(s) configured.")


if __name__ == "__main__":
    _config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    if len(sys.argv) > 1:
        _config_path = sys.argv[1]
    sync(_config_path)
