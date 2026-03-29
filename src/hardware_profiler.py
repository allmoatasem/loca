"""
hardware_profiler.py — hardware detection and model recommendations.

Primary path: run llmfit (auto-downloaded from GitHub releases if absent).
Fallback path: built-in cross-platform detection (macOS/Linux/Windows).

llmfit GitHub: https://github.com/AlexsJones/llmfit
"""
from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Where we install the llmfit binary (alongside our own src package)
_LLMFIT_DIR = Path(__file__).parent.parent / ".llmfit"
_LLMFIT_VERSION = "v0.8.5"
_LLMFIT_BASE = f"https://github.com/AlexsJones/llmfit/releases/download/{_LLMFIT_VERSION}"


@dataclass
class HardwareProfile:
    platform: str            # "darwin" | "linux" | "win32"
    arch: str                # "arm64" | "x86_64"
    cpu_name: str
    total_ram_gb: float
    available_ram_gb: float
    has_apple_silicon: bool
    has_nvidia_gpu: bool
    supports_mlx: bool
    llmfit_available: bool = False


@dataclass
class ModelRecommendation:
    name: str
    repo_id: str
    filename: str | None     # None for MLX snapshot downloads
    format: str              # "gguf" | "mlx"
    size_gb: float
    quant: str
    context: int
    why: str


# ---------------------------------------------------------------------------
# llmfit binary management
# ---------------------------------------------------------------------------

def _asset_name() -> tuple[str, bool]:
    """Return (asset_filename, is_zip) for the current platform."""
    sys_p = sys.platform
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"

    if sys_p == "darwin":
        return f"llmfit-{_LLMFIT_VERSION}-{arch}-apple-darwin.tar.gz", False
    if sys_p == "linux":
        return f"llmfit-{_LLMFIT_VERSION}-{arch}-unknown-linux-musl.tar.gz", False
    if sys_p == "win32":
        return f"llmfit-{_LLMFIT_VERSION}-{arch}-pc-windows-msvc.zip", True
    raise OSError(f"Unsupported platform for llmfit: {sys_p}")


def _binary_path() -> Path:
    name = "llmfit.exe" if sys.platform == "win32" else "llmfit"
    return _LLMFIT_DIR / name


def _llmfit_bin() -> str | None:
    """Return path to llmfit binary, or None if not available."""
    # Check PATH first (user may have installed it themselves)
    found = shutil.which("llmfit")
    if found:
        return found
    bp = _binary_path()
    if bp.exists():
        return str(bp)
    return None


def ensure_llmfit() -> str | None:
    """
    Ensure llmfit is available, downloading it if necessary.
    Returns the binary path or None on failure.
    """
    existing = _llmfit_bin()
    if existing:
        return existing

    try:
        asset, is_zip = _asset_name()
        url = f"{_LLMFIT_BASE}/{asset}"
        _LLMFIT_DIR.mkdir(parents=True, exist_ok=True)
        archive = _LLMFIT_DIR / asset
        logger.info(f"Downloading llmfit {_LLMFIT_VERSION} from {url}")
        urllib.request.urlretrieve(url, archive)

        if is_zip:
            with zipfile.ZipFile(archive) as z:
                for member in z.namelist():
                    if member.endswith(".exe") or member == "llmfit":
                        z.extract(member, _LLMFIT_DIR)
        else:
            with tarfile.open(archive) as t:
                for info in t.getmembers():
                    if info.name.endswith("llmfit") or info.name == "llmfit":
                        info.name = "llmfit"
                        t.extract(info, _LLMFIT_DIR)

        archive.unlink(missing_ok=True)
        bp = _binary_path()
        if bp.exists():
            bp.chmod(0o755)
            logger.info(f"llmfit installed at {bp}")
            return str(bp)
    except Exception as e:
        logger.warning(f"Could not auto-install llmfit: {e}")
    return None


def _run_llmfit(args: list[str], bin_path: str) -> dict | list | None:
    """Run llmfit with given args, return parsed JSON or None."""
    try:
        result = subprocess.run(
            [bin_path] + args + ["--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        logger.debug(f"llmfit run failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Fallback hardware detection (no llmfit)
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


def _fallback_profile() -> HardwareProfile:
    sys_p = sys.platform
    arch = platform.machine().lower()
    cpu_name = platform.processor() or "Unknown CPU"
    total_gb = 0.0
    available_gb = 0.0
    has_apple_silicon = False
    has_nvidia = False

    if sys_p == "darwin":
        raw = _sysctl("hw.memsize")
        total_gb = int(raw) / 1e9 if raw.isdigit() else 0.0
        cpu_name = _sysctl("machdep.cpu.brand_string") or cpu_name
        has_apple_silicon = arch in ("arm64", "aarch64")
        try:
            vm_out = subprocess.check_output(["vm_stat"], stderr=subprocess.DEVNULL, text=True)
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

    elif sys_p == "linux":
        mem_info = _read_proc("/proc/meminfo")
        for line in mem_info.splitlines():
            if line.startswith("MemTotal:"):
                total_gb = int(line.split()[1]) / 1e6
            elif line.startswith("MemAvailable:"):
                available_gb = int(line.split()[1]) / 1e6
        try:
            lscpu = subprocess.check_output(["lscpu"], stderr=subprocess.DEVNULL, text=True)
            for line in lscpu.splitlines():
                if "Model name" in line:
                    cpu_name = line.split(":")[1].strip()
                    break
        except Exception:
            pass
        try:
            subprocess.check_output(["nvidia-smi"], stderr=subprocess.DEVNULL)
            has_nvidia = True
        except Exception:
            pass

    elif sys_p == "win32":
        try:
            import psutil
            vm = psutil.virtual_memory()
            total_gb = vm.total / 1e9
            available_gb = vm.available / 1e9
        except ImportError:
            pass

    if total_gb == 0.0:
        try:
            import psutil
            vm = psutil.virtual_memory()
            total_gb = vm.total / 1e9
            available_gb = vm.available / 1e9
        except ImportError:
            pass

    return HardwareProfile(
        platform=sys_p,
        arch=arch,
        cpu_name=cpu_name,
        total_ram_gb=round(total_gb, 1),
        available_ram_gb=round(available_gb, 1),
        has_apple_silicon=has_apple_silicon,
        has_nvidia_gpu=has_nvidia,
        supports_mlx=has_apple_silicon,
        llmfit_available=False,
    )


# ---------------------------------------------------------------------------
# Fallback curated catalog (used only when llmfit is unavailable)
# ---------------------------------------------------------------------------

_CATALOG: list[tuple[float, float, ModelRecommendation]] = [
    (0, 6, ModelRecommendation("Phi-3.5-mini Q4_K_M", "bartowski/Phi-3.5-mini-instruct-GGUF", "Phi-3.5-mini-instruct-Q4_K_M.gguf", "gguf", 2.4, "Q4_K_M", 131072, "3.8B with 128k context; fits in 4 GB.")),
    (6, 12, ModelRecommendation("Qwen2.5-7B Q4_K_M", "bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "gguf", 4.7, "Q4_K_M", 32768, "Strong 7B; great coding and reasoning.")),
    (12, 24, ModelRecommendation("Qwen2.5-14B Q4_K_M", "bartowski/Qwen2.5-14B-Instruct-GGUF", "Qwen2.5-14B-Instruct-Q4_K_M.gguf", "gguf", 8.9, "Q4_K_M", 32768, "Best-in-class 14B for code and instructions.")),
    (12, 24, ModelRecommendation("Qwen2.5-14B 4-bit MLX", "mlx-community/Qwen2.5-14B-Instruct-4bit", None, "mlx", 8.5, "4-bit", 32768, "Native Apple Silicon; faster than llama.cpp on M-series.")),
    (24, 48, ModelRecommendation("Qwen2.5-32B Q4_K_M", "bartowski/Qwen2.5-32B-Instruct-GGUF", "Qwen2.5-32B-Instruct-Q4_K_M.gguf", "gguf", 19.8, "Q4_K_M", 32768, "32B powerhouse; strong reasoning.")),
    (24, 48, ModelRecommendation("Qwen2.5-32B 4-bit MLX", "mlx-community/Qwen2.5-32B-Instruct-4bit", None, "mlx", 18.0, "4-bit", 32768, "Best option on 32 GB Apple Silicon.")),
    (48, 9999, ModelRecommendation("Qwen2.5-72B Q4_K_M", "bartowski/Qwen2.5-72B-Instruct-GGUF", "Qwen2.5-72B-Instruct-Q4_K_M.gguf", "gguf", 43.0, "Q4_K_M", 32768, "Near-GPT-4 quality on your hardware.")),
    (48, 9999, ModelRecommendation("Qwen2.5-72B 4-bit MLX", "mlx-community/Qwen2.5-72B-Instruct-4bit", None, "mlx", 39.0, "4-bit", 32768, "Maximum quality on 64 GB+ Apple Silicon.")),
]


def _fallback_recommendations(profile: HardwareProfile) -> list[ModelRecommendation]:
    results = [r for lo, hi, r in _CATALOG if lo <= profile.total_ram_gb < hi]
    if not profile.supports_mlx:
        results = [r for r in results if r.format != "mlx"]
    results.sort(key=lambda r: (0 if r.format == "mlx" else 1, r.size_gb))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_hardware_profile() -> HardwareProfile:
    """Return hardware profile, using llmfit if available."""
    bin_path = _llmfit_bin()
    if bin_path:
        data = _run_llmfit(["system"], bin_path)
        if data and isinstance(data, dict):
            try:
                total = float(data.get("total_memory_gb") or data.get("memory_gb") or 0)
                available = float(data.get("available_memory_gb") or data.get("free_memory_gb") or 0)
                cpu = str(data.get("cpu") or data.get("cpu_model") or platform.processor() or "Unknown")
                arch = platform.machine().lower()
                has_apple = arch in ("arm64", "aarch64") and sys.platform == "darwin"
                has_nvidia = bool(data.get("gpus") or data.get("nvidia_gpus") or data.get("has_nvidia"))
                return HardwareProfile(
                    platform=sys.platform,
                    arch=arch,
                    cpu_name=cpu,
                    total_ram_gb=round(total, 1),
                    available_ram_gb=round(available, 1),
                    has_apple_silicon=has_apple,
                    has_nvidia_gpu=has_nvidia,
                    supports_mlx=has_apple,
                    llmfit_available=True,
                )
            except Exception:
                pass
    return _fallback_profile()


def get_recommendations(profile: HardwareProfile | None = None) -> list[ModelRecommendation]:
    """Return model recommendations. Uses llmfit if available, falls back to built-in catalog."""
    if profile is None:
        profile = get_hardware_profile()

    bin_path = _llmfit_bin()
    if bin_path:
        # Try recommend subcommand with limit 10
        data = _run_llmfit(["recommend", "--limit", "10"], bin_path)
        if data:
            recs = data if isinstance(data, list) else data.get("recommendations", [])
            results: list[ModelRecommendation] = []
            for item in recs:
                if not isinstance(item, dict):
                    continue
                # llmfit JSON field mapping
                name = str(item.get("name") or item.get("model_name") or "Unknown")
                repo = str(item.get("repo_id") or item.get("huggingface_repo") or item.get("repository") or "")
                fname = item.get("filename") or item.get("file") or None
                fmt_raw = str(item.get("format") or item.get("runtime") or "gguf").lower()
                fmt = "mlx" if "mlx" in fmt_raw else "gguf"
                if fmt == "mlx" and not profile.supports_mlx:
                    continue
                size = float(item.get("memory_required_gb") or item.get("size_gb") or item.get("vram_gb") or 0)
                quant = str(item.get("quantization") or item.get("quant") or "Q4_K_M")
                ctx = int(item.get("context_length") or item.get("context") or item.get("max_context") or 32768)
                why = str(item.get("reason") or item.get("description") or item.get("fit_reason") or "Recommended by llmfit for your hardware.")
                if not repo:
                    # Skip entries without a downloadable repo
                    continue
                results.append(ModelRecommendation(
                    name=name, repo_id=repo, filename=fname,
                    format=fmt, size_gb=size, quant=quant, context=ctx, why=why,
                ))
            if results:
                return results

    return _fallback_recommendations(profile)
