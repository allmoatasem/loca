"""
hardware_profiler.py — cross-platform hardware detection and model recommendations.

Detects RAM, CPU, Apple Silicon / NVIDIA GPU presence, and recommends GGUF/MLX
models from a curated catalog that fit within available memory.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    platform: str          # "darwin" | "linux" | "windows"
    arch: str              # "arm64" | "x86_64" | "amd64"
    cpu_name: str
    total_ram_gb: float
    available_ram_gb: float
    has_apple_silicon: bool
    has_nvidia_gpu: bool
    supports_mlx: bool     # arm64 Darwin only


@dataclass
class ModelRecommendation:
    name: str              # display name
    repo_id: str           # HuggingFace repo
    filename: str | None   # None for MLX (snapshot)
    format: str            # "gguf" | "mlx"
    size_gb: float         # approx disk / RAM footprint
    quant: str             # e.g. "Q4_K_M", "4-bit"
    context: int           # recommended ctx_size
    why: str               # one-line reason shown in UI


# ---------------------------------------------------------------------------
# Curated model catalog  — (min_ram_gb, max_ram_gb, entry)
# Ordered from smallest to largest so first-fit gives the best match.
# ---------------------------------------------------------------------------

_CATALOG: list[tuple[float, float, ModelRecommendation]] = [
    # ── 4 GB machines ──────────────────────────────────────────────────────
    (0, 6, ModelRecommendation(
        name="Qwen2.5-1.5B Q8 (GGUF)",
        repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        filename="qwen2.5-1.5b-instruct-q8_0.gguf",
        format="gguf",
        size_gb=1.7,
        quant="Q8_0",
        context=32768,
        why="Tiny but surprisingly capable; fits in 4 GB RAM.",
    )),
    (0, 6, ModelRecommendation(
        name="Phi-3.5-mini Q4_K_M (GGUF)",
        repo_id="bartowski/Phi-3.5-mini-instruct-GGUF",
        filename="Phi-3.5-mini-instruct-Q4_K_M.gguf",
        format="gguf",
        size_gb=2.4,
        quant="Q4_K_M",
        context=131072,
        why="Microsoft 3.8B with 128k context; runs well in 4 GB.",
    )),
    # ── 8 GB machines ──────────────────────────────────────────────────────
    (6, 12, ModelRecommendation(
        name="Qwen2.5-7B Q4_K_M (GGUF)",
        repo_id="bartowski/Qwen2.5-7B-Instruct-GGUF",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        format="gguf",
        size_gb=4.7,
        quant="Q4_K_M",
        context=32768,
        why="Excellent 7B; strong coding and reasoning at Q4.",
    )),
    (6, 12, ModelRecommendation(
        name="Llama-3.2-3B Q8 (GGUF)",
        repo_id="bartowski/Llama-3.2-3B-Instruct-GGUF",
        filename="Llama-3.2-3B-Instruct-Q8_0.gguf",
        format="gguf",
        size_gb=3.4,
        quant="Q8_0",
        context=131072,
        why="Meta 3B at full 8-bit quality; fast for the size.",
    )),
    # ── 16 GB machines ─────────────────────────────────────────────────────
    (12, 24, ModelRecommendation(
        name="Qwen2.5-14B Q4_K_M (GGUF)",
        repo_id="bartowski/Qwen2.5-14B-Instruct-GGUF",
        filename="Qwen2.5-14B-Instruct-Q4_K_M.gguf",
        format="gguf",
        size_gb=8.9,
        quant="Q4_K_M",
        context=32768,
        why="Best-in-class 14B; code and instruction following.",
    )),
    (12, 24, ModelRecommendation(
        name="Mistral-Nemo-12B Q4_K_M (GGUF)",
        repo_id="bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        filename="Mistral-Nemo-Instruct-2407-Q4_K_M.gguf",
        format="gguf",
        size_gb=7.1,
        quant="Q4_K_M",
        context=128000,
        why="Mistral 12B with 128k context window.",
    )),
    # ── 16 GB Apple Silicon (MLX variants) ─────────────────────────────────
    (12, 24, ModelRecommendation(
        name="Qwen2.5-14B 4-bit (MLX)",
        repo_id="mlx-community/Qwen2.5-14B-Instruct-4bit",
        filename=None,
        format="mlx",
        size_gb=8.5,
        quant="4-bit",
        context=32768,
        why="Native Apple Silicon; faster than llama.cpp on M-series.",
    )),
    # ── 32 GB machines ─────────────────────────────────────────────────────
    (24, 48, ModelRecommendation(
        name="Qwen2.5-32B Q4_K_M (GGUF)",
        repo_id="bartowski/Qwen2.5-32B-Instruct-GGUF",
        filename="Qwen2.5-32B-Instruct-Q4_K_M.gguf",
        format="gguf",
        size_gb=19.8,
        quant="Q4_K_M",
        context=32768,
        why="32B powerhouse; great reasoning and long-form output.",
    )),
    (24, 48, ModelRecommendation(
        name="Qwen2.5-32B 4-bit (MLX)",
        repo_id="mlx-community/Qwen2.5-32B-Instruct-4bit",
        filename=None,
        format="mlx",
        size_gb=18.0,
        quant="4-bit",
        context=32768,
        why="32B via MLX; best option on 32 GB Apple Silicon.",
    )),
    # ── 64 GB+ machines ────────────────────────────────────────────────────
    (48, 9999, ModelRecommendation(
        name="Qwen2.5-72B Q4_K_M (GGUF)",
        repo_id="bartowski/Qwen2.5-72B-Instruct-GGUF",
        filename="Qwen2.5-72B-Instruct-Q4_K_M.gguf",
        format="gguf",
        size_gb=43.0,
        quant="Q4_K_M",
        context=32768,
        why="72B flagship; near-GPT-4 quality on your hardware.",
    )),
    (48, 9999, ModelRecommendation(
        name="Qwen2.5-72B 4-bit (MLX)",
        repo_id="mlx-community/Qwen2.5-72B-Instruct-4bit",
        filename=None,
        format="mlx",
        size_gb=39.0,
        quant="4-bit",
        context=32768,
        why="72B via MLX; maximum quality on 64 GB+ Apple Silicon.",
    )),
]


# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------

def _sysctl(key: str) -> str:
    try:
        return subprocess.check_output(
            ["sysctl", "-n", key], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return ""


def _read_proc(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _wmic(query: str) -> str:
    try:
        return subprocess.check_output(
            ["wmic"] + query.split(), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return ""


def _psutil_ram() -> tuple[float, float]:
    """Returns (total_gb, available_gb) via psutil if available."""
    try:
        import psutil  # optional dependency
        vm = psutil.virtual_memory()
        return vm.total / 1e9, vm.available / 1e9
    except ImportError:
        return 0.0, 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_hardware_profile() -> HardwareProfile:
    sys_platform = sys.platform  # "darwin" | "linux" | "win32"
    arch = platform.machine().lower()  # "arm64" | "x86_64" | "amd64"
    cpu_name = platform.processor() or "Unknown CPU"
    total_gb = 0.0
    available_gb = 0.0
    has_apple_silicon = False
    has_nvidia = False

    if sys_platform == "darwin":
        raw = _sysctl("hw.memsize")
        total_gb = int(raw) / 1e9 if raw.isdigit() else 0.0
        cpu_name = _sysctl("machdep.cpu.brand_string") or cpu_name
        has_apple_silicon = arch == "arm64"
        # available RAM via vm_stat
        try:
            vm_out = subprocess.check_output(
                ["vm_stat"], stderr=subprocess.DEVNULL, text=True
            )
            page_size = 4096
            free_pages = 0
            for line in vm_out.splitlines():
                if "Pages free" in line or "Pages speculative" in line:
                    parts = line.split(":")
                    if len(parts) == 2:
                        free_pages += int(parts[1].strip().rstrip("."))
            available_gb = (free_pages * page_size) / 1e9
        except Exception:
            pass
        if available_gb == 0.0:
            _, available_gb = _psutil_ram()

    elif sys_platform == "linux":
        mem_info = _read_proc("/proc/meminfo")
        for line in mem_info.splitlines():
            if line.startswith("MemTotal:"):
                total_gb = int(line.split()[1]) / 1e6
            elif line.startswith("MemAvailable:"):
                available_gb = int(line.split()[1]) / 1e6
        try:
            cpu_name = subprocess.check_output(
                ["lscpu"], stderr=subprocess.DEVNULL, text=True
            )
            for line in cpu_name.splitlines():
                if "Model name" in line:
                    cpu_name = line.split(":")[1].strip()
                    break
            else:
                cpu_name = platform.processor() or "Unknown CPU"
        except Exception:
            cpu_name = platform.processor() or "Unknown CPU"
        # NVIDIA GPU check
        try:
            subprocess.check_output(
                ["nvidia-smi"], stderr=subprocess.DEVNULL
            )
            has_nvidia = True
        except Exception:
            has_nvidia = False

    elif sys_platform == "win32":
        total_gb, available_gb = _psutil_ram()
        if total_gb == 0.0:
            raw = _wmic("ComputerSystem get TotalPhysicalMemory /value")
            for line in raw.splitlines():
                if "TotalPhysicalMemory=" in line:
                    try:
                        total_gb = int(line.split("=")[1].strip()) / 1e9
                    except ValueError:
                        pass
        cpu_name = platform.processor() or "Unknown CPU"
        try:
            subprocess.check_output(
                ["nvidia-smi"], stderr=subprocess.DEVNULL
            )
            has_nvidia = True
        except Exception:
            has_nvidia = False

    if total_gb == 0.0:
        total_gb, available_gb = _psutil_ram()

    return HardwareProfile(
        platform=sys_platform,
        arch=arch,
        cpu_name=cpu_name,
        total_ram_gb=round(total_gb, 1),
        available_ram_gb=round(available_gb, 1),
        has_apple_silicon=has_apple_silicon,
        has_nvidia_gpu=has_nvidia,
        supports_mlx=has_apple_silicon,
    )


def get_recommendations(profile: HardwareProfile | None = None) -> list[ModelRecommendation]:
    """Return models that fit the hardware, MLX-first on Apple Silicon."""
    if profile is None:
        profile = get_hardware_profile()

    budget = profile.total_ram_gb
    results: list[ModelRecommendation] = []

    for min_r, max_r, rec in _CATALOG:
        if min_r <= budget < max_r:
            # Skip MLX models on non-Apple-Silicon
            if rec.format == "mlx" and not profile.supports_mlx:
                continue
            results.append(rec)

    # On Apple Silicon, put MLX options first
    if profile.has_apple_silicon:
        results.sort(key=lambda r: (0 if r.format == "mlx" else 1, r.size_gb))
    else:
        results.sort(key=lambda r: r.size_gb)

    return results
