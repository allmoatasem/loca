"""
Adapter discovery — LoRA adapters live next to the base model they were
trained on, under `{models_dir}/<base-model>/adapters/<adapter-name>/`.
Each adapter directory is what `mlx_lm.lora` writes at train time:
weights + `adapter_config.json`.

Tying adapters to a specific base model on disk is deliberate. MLX LoRA
adapters are architecturally locked to the base they were trained on
(tensor shapes don't transfer between Qwen and Llama), so the filesystem
layout enforces the compatibility invariant before the server even sees
the request.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AdapterInfo:
    name: str                 # directory name under adapters/
    path: str                 # absolute path to adapter directory
    base_model: str           # display name of the base model it lives under
    size_mb: float
    rank: int | None = None   # LoRA rank, from adapter_config.json
    alpha: float | None = None
    trained_at: float | None = None  # POSIX timestamp from config mtime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "base_model": self.base_model,
            "size_mb": round(self.size_mb, 2),
            "rank": self.rank,
            "alpha": self.alpha,
            "trained_at": self.trained_at,
        }


def _dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


def _read_adapter_config(adapter_dir: Path) -> dict:
    """Best-effort read of `adapter_config.json`. Returns empty dict on any
    error — a malformed or missing config shouldn't hide the adapter from
    the list; the UI can still show its name and size."""
    cfg_path = adapter_dir / "adapter_config.json"
    try:
        return json.loads(cfg_path.read_text())
    except (OSError, ValueError):
        return {}


def list_adapters(model_dir: Path | str, base_model_name: str) -> list[AdapterInfo]:
    """Return all adapters found under `<model_dir>/adapters/`.

    A directory counts as an adapter if it's non-empty. The caller owns
    the definition of `base_model_name` — typically the model's display
    name (e.g. "Qwen2.5-7B-Instruct") — so the UI can show "Qwen2.5-7B
    + writing-voice".
    """
    base = Path(model_dir) / "adapters"
    if not base.is_dir():
        return []
    out: list[AdapterInfo] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        cfg = _read_adapter_config(entry)
        rank = cfg.get("lora_parameters", {}).get("rank") if isinstance(cfg, dict) else None
        alpha = cfg.get("lora_parameters", {}).get("alpha") if isinstance(cfg, dict) else None
        trained_at: float | None = None
        # Prefer the config's mtime over the directory's; the directory
        # mtime also moves when sub-files are touched (e.g. a re-scan).
        cfg_path = entry / "adapter_config.json"
        try:
            if cfg_path.is_file():
                trained_at = cfg_path.stat().st_mtime
            else:
                trained_at = entry.stat().st_mtime
        except OSError:
            pass
        try:
            size_mb = _dir_size_mb(entry)
        except OSError:
            size_mb = 0.0
        out.append(AdapterInfo(
            name=entry.name,
            path=str(entry),
            base_model=base_model_name,
            size_mb=size_mb,
            rank=rank,
            alpha=alpha,
            trained_at=trained_at,
        ))
    return out


def resolve_adapter_path(
    model_dir: Path | str, adapter_name: str,
) -> Path | None:
    """Return the absolute path to an adapter by name, or None if missing.
    The caller should treat None as "adapter does not exist for this model"
    and surface that to the UI rather than silently continuing with no
    adapter — an incorrect load would confuse the user about what's active."""
    candidate = Path(model_dir) / "adapters" / adapter_name
    return candidate if candidate.is_dir() else None
