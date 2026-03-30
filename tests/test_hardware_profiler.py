"""
Tests for hardware_profiler.py.

Covers:
  - Provider inference from repo_id
  - llmfit binary path resolution (PATH, local dir, missing)
  - Hardware profile parsing from llmfit JSON output
  - Fallback hardware profile when llmfit is absent
  - Recommendations from llmfit output (MLX/GGUF filtering, why field)
  - Fallback catalog recommendations (RAM-based filtering, no-MLX filter)
  - Asset name resolution per platform

Run with: pytest tests/test_hardware_profiler.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.hardware_profiler import (
    HardwareProfile,
    ModelRecommendation,
    _asset_name,
    _fallback_recommendations,
    _infer_provider,
    _llmfit_bin,
    _run_llmfit,
    get_hardware_profile,
    get_recommendations,
)


# ---------------------------------------------------------------------------
# Provider inference
# ---------------------------------------------------------------------------

class TestInferProvider:
    def test_known_owners(self):
        assert _infer_provider("mlx-community/Qwen2.5-7B") == "MLX Community"
        assert _infer_provider("bartowski/Phi-3-GGUF") == "Bartowski (GGUF)"
        assert _infer_provider("meta-llama/Llama-3") == "Meta"
        assert _infer_provider("mistralai/Mistral-7B") == "Mistral"
        assert _infer_provider("google/gemma-2b") == "Google"
        assert _infer_provider("microsoft/phi-3.5") == "Microsoft"
        assert _infer_provider("deepseek-ai/deepseek-coder") == "DeepSeek"
        assert _infer_provider("nvidia/llama-3.1-nemotron") == "NVIDIA"
        assert _infer_provider("qwen/Qwen2.5-7B") == "Alibaba"

    def test_unknown_owner_capitalised(self):
        result = _infer_provider("some-org/some-model")
        assert result == "Some Org"

    def test_no_slash_returns_empty(self):
        assert _infer_provider("justmodelname") == ""

    def test_prefix_matching(self):
        # "deepseek" prefix should match even if owner has suffix
        assert _infer_provider("deepseek-coder/deepseek-7b") == "DeepSeek"


# ---------------------------------------------------------------------------
# llmfit binary resolution
# ---------------------------------------------------------------------------

class TestLlmfitBin:
    def test_finds_binary_in_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/llmfit"):
            result = _llmfit_bin()
        assert result == "/usr/local/bin/llmfit"

    def test_finds_local_binary(self, tmp_path):
        # Simulate the binary existing in .llmfit/
        bin_file = tmp_path / "llmfit"
        bin_file.touch()
        with patch("shutil.which", return_value=None), \
             patch("src.hardware_profiler._binary_path", return_value=bin_file):
            result = _llmfit_bin()
        assert result == str(bin_file)

    def test_returns_none_when_not_found(self):
        with patch("shutil.which", return_value=None), \
             patch("src.hardware_profiler._binary_path") as mock_bp:
            mock_bp.return_value = MagicMock(exists=MagicMock(return_value=False))
            result = _llmfit_bin()
        assert result is None


# ---------------------------------------------------------------------------
# _run_llmfit
# ---------------------------------------------------------------------------

class TestRunLlmfit:
    def test_parses_json_output(self):
        fake_output = '{"models": [{"name": "test"}]}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
            result = _run_llmfit(["recommend", "--limit", "5"], "/fake/llmfit")
        assert result == {"models": [{"name": "test"}]}

    def test_returns_none_on_nonzero_exit(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _run_llmfit(["recommend"], "/fake/llmfit")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("binary not found")):
            result = _run_llmfit(["recommend"], "/fake/llmfit")
        assert result is None

    def test_returns_none_on_empty_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="   ")
            result = _run_llmfit(["system"], "/fake/llmfit")
        assert result is None


# ---------------------------------------------------------------------------
# get_hardware_profile
# ---------------------------------------------------------------------------

class TestGetHardwareProfile:
    def test_uses_llmfit_when_available(self):
        llmfit_output = {
            "system": {
                "total_ram_gb": 36,
                "available_ram_gb": 20,
                "cpu_name": "Apple M3 Pro",
                "gpus": [],
            }
        }
        with patch("src.hardware_profiler._llmfit_bin", return_value="/usr/local/bin/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output), \
             patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            profile = get_hardware_profile()

        assert profile.total_ram_gb == 36.0
        assert profile.available_ram_gb == 20.0
        assert profile.cpu_name == "Apple M3 Pro"
        assert profile.has_apple_silicon is True
        assert profile.supports_mlx is True
        assert profile.llmfit_available is True

    def test_detects_nvidia_gpu(self):
        llmfit_output = {
            "system": {
                "total_ram_gb": 32,
                "available_ram_gb": 16,
                "cpu_name": "Intel i9",
                "gpus": [{"backend": "CUDA", "name": "RTX 4090"}],
            }
        }
        with patch("src.hardware_profiler._llmfit_bin", return_value="/usr/local/bin/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output), \
             patch("platform.machine", return_value="x86_64"), \
             patch("sys.platform", "linux"):
            profile = get_hardware_profile()

        assert profile.has_nvidia_gpu is True
        assert profile.has_apple_silicon is False

    def test_metal_gpu_not_counted_as_nvidia(self):
        llmfit_output = {
            "system": {
                "total_ram_gb": 16,
                "available_ram_gb": 8,
                "cpu_name": "Apple M1",
                "gpus": [{"backend": "Metal", "name": "M1"}],
            }
        }
        with patch("src.hardware_profiler._llmfit_bin", return_value="/usr/local/bin/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output), \
             patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            profile = get_hardware_profile()

        assert profile.has_nvidia_gpu is False

    def test_falls_back_when_llmfit_unavailable(self):
        with patch("src.hardware_profiler._llmfit_bin", return_value=None), \
             patch("src.hardware_profiler._fallback_profile") as mock_fallback:
            mock_fallback.return_value = HardwareProfile(
                platform="darwin", arch="arm64", cpu_name="Apple M1",
                total_ram_gb=16.0, available_ram_gb=8.0,
                has_apple_silicon=True, has_nvidia_gpu=False,
                supports_mlx=True, llmfit_available=False,
            )
            profile = get_hardware_profile()

        assert profile.llmfit_available is False
        mock_fallback.assert_called_once()

    def test_falls_back_on_llmfit_parse_failure(self):
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=None), \
             patch("src.hardware_profiler._fallback_profile") as mock_fallback:
            mock_fallback.return_value = HardwareProfile(
                platform="linux", arch="x86_64", cpu_name="Intel i9",
                total_ram_gb=32.0, available_ram_gb=16.0,
                has_apple_silicon=False, has_nvidia_gpu=False,
                supports_mlx=False, llmfit_available=False,
            )
            profile = get_hardware_profile()

        mock_fallback.assert_called_once()


# ---------------------------------------------------------------------------
# get_recommendations
# ---------------------------------------------------------------------------

class TestGetRecommendations:
    def _apple_silicon_profile(self, ram_gb: float = 36.0) -> HardwareProfile:
        return HardwareProfile(
            platform="darwin", arch="arm64", cpu_name="Apple M3 Pro",
            total_ram_gb=ram_gb, available_ram_gb=ram_gb * 0.6,
            has_apple_silicon=True, has_nvidia_gpu=False,
            supports_mlx=True, llmfit_available=True,
        )

    def _linux_profile(self, ram_gb: float = 32.0) -> HardwareProfile:
        return HardwareProfile(
            platform="linux", arch="x86_64", cpu_name="Intel i9",
            total_ram_gb=ram_gb, available_ram_gb=ram_gb * 0.5,
            has_apple_silicon=False, has_nvidia_gpu=True,
            supports_mlx=False, llmfit_available=True,
        )

    def test_parses_mlx_recommendation(self):
        llmfit_output = {"models": [{
            "name": "mlx-community/Qwen2.5-14B-Instruct-4bit",
            "runtime": "mlx",
            "memory_required_gb": 8.5,
            "best_quant": "4-bit",
            "context_length": 32768,
            "score": 90.0,
            "fit_level": "Perfect Fit",
            "estimated_tps": 45.0,
            "use_case": "general",
            "notes": "Fast on Apple Silicon with unified memory.",
        }]}
        profile = self._apple_silicon_profile()
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        assert len(recs) == 1
        r = recs[0]
        assert r.format == "mlx"
        assert r.repo_id == "mlx-community/Qwen2.5-14B-Instruct-4bit"
        assert r.filename is None   # MLX repos have no single file
        assert r.size_gb == 8.5
        assert r.fit_level == "Perfect Fit"
        assert r.tps == 45.0
        assert r.why == "Fast on Apple Silicon with unified memory."

    def test_parses_gguf_recommendation_with_source(self):
        llmfit_output = {"models": [{
            "name": "bartowski/Qwen2.5-14B-Instruct-GGUF",
            "runtime": "gguf",
            "memory_required_gb": 8.9,
            "best_quant": "Q4_K_M",
            "context_length": 32768,
            "score": 85.0,
            "fit_level": "Good Fit",
            "estimated_tps": 30.0,
            "use_case": "code",
            "notes": "Best-in-class 14B.",
            "gguf_sources": [{"repo": "bartowski/Qwen2.5-14B-Instruct-GGUF", "file": "Qwen2.5-14B-Instruct-Q4_K_M.gguf"}],
        }]}
        profile = self._linux_profile()
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        assert len(recs) == 1
        r = recs[0]
        assert r.format == "gguf"
        assert r.filename == "Qwen2.5-14B-Instruct-Q4_K_M.gguf"
        assert r.provider == "Bartowski (GGUF)"

    def test_filters_mlx_on_non_apple_silicon(self):
        llmfit_output = {"models": [
            {
                "name": "mlx-community/Qwen2.5-7B-Instruct-4bit",
                "runtime": "mlx",
                "memory_required_gb": 4.2,
                "best_quant": "4-bit",
                "context_length": 32768,
                "score": 80.0,
                "notes": "MLX model",
            },
            {
                "name": "bartowski/Qwen2.5-7B-Instruct-GGUF",
                "runtime": "gguf",
                "memory_required_gb": 4.7,
                "best_quant": "Q4_K_M",
                "context_length": 32768,
                "score": 78.0,
                "notes": "GGUF model",
            },
        ]}
        profile = self._linux_profile()  # no Apple Silicon
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        assert all(r.format != "mlx" for r in recs), "MLX models should be filtered on non-Apple Silicon"
        assert len(recs) == 1
        assert recs[0].format == "gguf"

    def test_skips_entries_without_hf_slash(self):
        """Entries where repo_id has no '/' are not valid HF repos — skip them."""
        llmfit_output = {"models": [
            {
                "name": "invalid-model-no-slash",
                "runtime": "gguf",
                "memory_required_gb": 4.0,
                "best_quant": "Q4_K_M",
                "context_length": 4096,
                "score": 70.0,
                "notes": "",
            },
            {
                "name": "bartowski/ValidModel-GGUF",
                "runtime": "gguf",
                "memory_required_gb": 4.0,
                "best_quant": "Q4_K_M",
                "context_length": 4096,
                "score": 70.0,
                "notes": "Valid",
            },
        ]}
        profile = self._linux_profile()
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        assert len(recs) == 1
        assert recs[0].repo_id == "bartowski/ValidModel-GGUF"

    def test_why_field_from_list_of_notes(self):
        """llmfit sometimes returns notes as a list — should be joined with ' · '."""
        llmfit_output = {"models": [{
            "name": "mlx-community/Some-Model",
            "runtime": "mlx",
            "memory_required_gb": 5.0,
            "best_quant": "4-bit",
            "context_length": 32768,
            "score": 88.0,
            "notes": ["Great for coding", "Fast on M2", "128k context"],
        }]}
        profile = self._apple_silicon_profile()
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        assert recs[0].why == "Great for coding · Fast on M2 · 128k context"

    def test_falls_back_to_catalog_when_llmfit_absent(self):
        profile = HardwareProfile(
            platform="darwin", arch="arm64", cpu_name="Apple M1",
            total_ram_gb=16.0, available_ram_gb=8.0,
            has_apple_silicon=True, has_nvidia_gpu=False,
            supports_mlx=True, llmfit_available=False,
        )
        with patch("src.hardware_profiler._llmfit_bin", return_value=None):
            recs = get_recommendations(profile)

        assert len(recs) > 0
        # Catalog should include entries for 12–24 GB range (16 GB fits)
        for r in recs:
            assert "/" in r.repo_id

    def test_falls_back_to_catalog_when_llmfit_returns_empty(self):
        llmfit_output = {"models": []}
        profile = HardwareProfile(
            platform="linux", arch="x86_64", cpu_name="Intel i9",
            total_ram_gb=32.0, available_ram_gb=16.0,
            has_apple_silicon=False, has_nvidia_gpu=True,
            supports_mlx=False, llmfit_available=True,
        )
        with patch("src.hardware_profiler._llmfit_bin", return_value="/fake/llmfit"), \
             patch("src.hardware_profiler._run_llmfit", return_value=llmfit_output):
            recs = get_recommendations(profile)

        # Should fall back to catalog
        assert len(recs) > 0


# ---------------------------------------------------------------------------
# Fallback catalog
# ---------------------------------------------------------------------------

class TestFallbackRecommendations:
    def _profile(self, ram: float, silicon: bool = False) -> HardwareProfile:
        return HardwareProfile(
            platform="darwin" if silicon else "linux",
            arch="arm64" if silicon else "x86_64",
            cpu_name="CPU",
            total_ram_gb=ram,
            available_ram_gb=ram * 0.5,
            has_apple_silicon=silicon,
            has_nvidia_gpu=not silicon,
            supports_mlx=silicon,
            llmfit_available=False,
        )

    def test_4gb_gets_small_model(self):
        recs = _fallback_recommendations(self._profile(4.0))
        assert len(recs) > 0
        for r in recs:
            assert r.size_gb < 6.0

    def test_mlx_excluded_on_non_apple_silicon(self):
        recs = _fallback_recommendations(self._profile(16.0, silicon=False))
        assert all(r.format != "mlx" for r in recs)

    def test_mlx_included_on_apple_silicon(self):
        recs = _fallback_recommendations(self._profile(32.0, silicon=True))
        formats = {r.format for r in recs}
        assert "mlx" in formats

    def test_provider_backfilled(self):
        recs = _fallback_recommendations(self._profile(16.0, silicon=True))
        for r in recs:
            assert r.provider != "", f"Provider should be set for {r.repo_id}"


# ---------------------------------------------------------------------------
# Asset name (platform detection)
# ---------------------------------------------------------------------------

class TestAssetName:
    def test_darwin_arm64(self):
        with patch("sys.platform", "darwin"), \
             patch("platform.machine", return_value="arm64"):
            name, is_zip = _asset_name()
        assert "aarch64" in name or "arm64" in name
        assert name.endswith(".tar.gz")
        assert is_zip is False

    def test_linux_x86_64(self):
        with patch("sys.platform", "linux"), \
             patch("platform.machine", return_value="x86_64"):
            name, is_zip = _asset_name()
        assert "x86_64" in name
        assert "linux" in name
        assert is_zip is False

    def test_windows(self):
        with patch("sys.platform", "win32"), \
             patch("platform.machine", return_value="AMD64"):
            name, is_zip = _asset_name()
        assert name.endswith(".zip")
        assert is_zip is True

    def test_unsupported_platform_raises(self):
        with patch("sys.platform", "freebsd"), \
             patch("platform.machine", return_value="x86_64"):
            with pytest.raises(OSError, match="Unsupported platform"):
                _asset_name()
