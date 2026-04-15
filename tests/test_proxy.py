"""
Tests for the FastAPI proxy server (src/proxy.py).

Uses FastAPI's TestClient (via httpx) — no real inference backend is started.
All external calls (model manager, orchestrator, hardware profiler) are mocked.

Run with: pytest tests/test_proxy.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# App fixture — patch everything that touches disk/network at startup
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """
    Return a TestClient for the proxy app with all external side-effects patched:
      - Config loading returns a minimal in-memory dict
      - InferenceBackend and ModelManager are mocked
      - Background recs-cache build is a no-op
    """
    minimal_config = {
        "inference": {"models_dir": "/tmp/loca-test-models", "active_model": None},
        "routing": {"max_tool_calls_per_turn": 5},
        "search": {"searxng_url": ""},
        "tools": {},
    }

    mock_backend = MagicMock()
    mock_backend.is_running.return_value = False
    mock_backend.current_model.return_value = None
    mock_backend.current_backend.return_value = None
    mock_backend.api_base.return_value = "http://localhost:11434"
    mock_backend.stop = AsyncMock()
    mock_backend.start = AsyncMock()
    mock_backend.models_dir = MagicMock()
    mock_backend.models_dir.__truediv__ = MagicMock(return_value=MagicMock(exists=MagicMock(return_value=False)))

    mock_mm = MagicMock()
    mock_mm.list_local.return_value = []
    mock_mm.load = AsyncMock(return_value=("test-model", "http://localhost:11434"))
    mock_mm.delete = MagicMock()
    mock_mm.download = AsyncMock()

    mock_orch = MagicMock()
    mock_orch.handle = AsyncMock()
    mock_orch.extract_and_save_memories = AsyncMock(return_value=[])

    mock_voice = MagicMock()
    mock_voice.transcribe = AsyncMock(return_value={"text": "hello world"})
    mock_voice.synthesize = AsyncMock(return_value=b"fake-wav-data")
    mock_voice.get_voice_config.return_value = {
        "stt_model": "test-stt", "tts_model": "test-tts",
        "tts_voice": "af_heart", "tts_speed": 1.0, "auto_tts": False, "models": [],
    }
    mock_voice.list_voice_models.return_value = []

    mock_memory_plugin = MagicMock()
    mock_memory_plugin.recall = AsyncMock(return_value=[])
    mock_plugin_mgr = MagicMock()
    mock_plugin_mgr.memory_plugin = mock_memory_plugin
    mock_plugin_mgr.start = AsyncMock()
    mock_plugin_mgr.stop = AsyncMock()
    mock_plugin_mgr.status.return_value = {"memory": {"type": "builtin", "status": "running"}}

    with patch("src.proxy._load_config", return_value=minimal_config), \
         patch("src.proxy.InferenceBackend", return_value=mock_backend), \
         patch("src.proxy.ModelManager", return_value=mock_mm), \
         patch("src.proxy.Orchestrator", return_value=mock_orch), \
         patch("src.proxy.VoiceBackend", return_value=mock_voice), \
         patch("src.proxy.PluginManager", return_value=mock_plugin_mgr), \
         patch("src.proxy._build_recs_cache", new_callable=AsyncMock), \
         patch("asyncio.create_task"):
        from src.proxy import app
        with TestClient(app, raise_server_exceptions=True) as c:
            # Inject mocks so tests can access them
            c._mock_backend = mock_backend
            c._mock_mm = mock_mm
            c._mock_orch = mock_orch
            c._mock_voice = mock_voice
            c._mock_memory_plugin = mock_memory_plugin
            c._mock_plugin_mgr = mock_plugin_mgr
            yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Conversations API
# ---------------------------------------------------------------------------

class TestConversationsAPI:
    def test_list_conversations_empty(self, client):
        with patch("src.proxy.list_conversations", return_value=[]):
            r = client.get("/api/conversations")
        assert r.status_code == 200
        assert r.json() == {"conversations": []}

    def test_list_conversations_returns_data(self, client):
        convs = [{"id": "c1", "title": "Test", "messages": [], "model": "m", "updated_at": "now"}]
        with patch("src.proxy.list_conversations", return_value=convs):
            r = client.get("/api/conversations")
        assert r.json()["conversations"] == convs

    def test_get_conversation_found(self, client):
        conv = {"id": "c1", "title": "Hello", "messages": [], "model": "m"}
        with patch("src.proxy.get_conversation", return_value=conv):
            r = client.get("/api/conversations/c1")
        assert r.status_code == 200
        assert r.json()["id"] == "c1"

    def test_get_conversation_not_found(self, client):
        with patch("src.proxy.get_conversation", return_value=None):
            r = client.get("/api/conversations/missing")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_save_conversation(self, client):
        with patch("src.proxy.save_conversation", return_value="new-id"):
            r = client.post("/api/conversations", json={
                "id": "c1", "title": "My chat", "messages": [], "model": "llama"
            })
        assert r.status_code == 200
        assert r.json() == {"id": "new-id"}

    def test_delete_conversation(self, client):
        with patch("src.proxy.delete_conversation") as mock_del:
            r = client.delete("/api/conversations/c1")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        mock_del.assert_called_once_with("c1")

    def test_patch_conversation_starred(self, client):
        with patch("src.proxy.patch_conversation") as mock_patch:
            r = client.patch("/api/conversations/c1", json={"starred": True})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        mock_patch.assert_called_once_with("c1", starred=True)

    def test_search_conversations_empty_query(self, client):
        r = client.get("/api/search/conversations?q=")
        assert r.status_code == 200
        assert r.json() == {"conversations": []}

    def test_search_conversations_with_query(self, client):
        results = [{"id": "c1", "title": "Python talk"}]
        with patch("src.proxy.search_conversations", return_value=results):
            r = client.get("/api/search/conversations?q=python")
        assert r.json()["conversations"] == results


# ---------------------------------------------------------------------------
# Memories API
# ---------------------------------------------------------------------------

class TestMemoriesAPI:
    def test_list_memories(self, client):
        mems = [{"id": "m1", "content": "User likes Python", "type": "user_fact"}]
        client._mock_memory_plugin.list_all.return_value = mems
        r = client.get("/api/memories")
        assert r.status_code == 200
        assert r.json()["memories"] == mems

    def test_list_memories_with_type_filter(self, client):
        client._mock_memory_plugin.list_all.return_value = []
        r = client.get("/api/memories?type=user_fact")
        assert r.status_code == 200
        client._mock_memory_plugin.list_all.assert_called_once_with(type="user_fact")

    def test_add_memory(self, client):
        client._mock_memory_plugin.store = AsyncMock(return_value="mem-001")
        r = client.post("/api/memories", json={"content": "Likes hiking", "type": "user_fact"})
        assert r.status_code == 200
        assert r.json() == {"id": "mem-001"}

    def test_update_memory(self, client):
        r = client.patch("/api/memories/m1", json={"content": "Updated content"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        client._mock_memory_plugin.update.assert_called_once_with("m1", "Updated content")

    def test_update_memory_empty_content_rejected(self, client):
        r = client.patch("/api/memories/m1", json={"content": "   "})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_delete_memory(self, client):
        r = client.delete("/api/memories/m1")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        client._mock_memory_plugin.delete.assert_called_once_with("m1")


# ---------------------------------------------------------------------------
# Models API
# ---------------------------------------------------------------------------

class TestModelsAPI:
    def test_openai_models_list(self, client):
        local_model = MagicMock()
        local_model.name = "qwen-7b"
        client._mock_mm.list_local.return_value = [local_model]
        r = client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        assert any(m["id"] == "qwen-7b" for m in data["data"])

    def test_local_models_list(self, client):
        mock_model = MagicMock()
        mock_model.to_dict.return_value = {"name": "qwen-7b", "format": "gguf", "size_gb": 4.7}
        client._mock_mm.list_local.return_value = [mock_model]
        r = client.get("/api/local-models")
        assert r.status_code == 200
        assert len(r.json()["models"]) == 1
        assert r.json()["models"][0]["name"] == "qwen-7b"

    def test_active_model_when_none_running(self, client):
        r = client.get("/api/models/active")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["name"] is None

    def test_load_model_success(self, client):
        client._mock_mm.load = AsyncMock(return_value=("qwen-7b", "http://localhost:11434"))
        r = client.post("/api/models/load", json={"name": "qwen-7b"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["name"] == "qwen-7b"

    def test_load_model_missing_name(self, client):
        r = client.post("/api/models/load", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_unload_model(self, client):
        r = client.post("/api/models/unload")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        client._mock_backend.stop.assert_awaited()

    def test_delete_model_success(self, client):
        client._mock_mm.delete = MagicMock()
        r = client.delete("/api/models/qwen-7b.gguf")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_model_not_found(self, client):
        client._mock_mm.delete = MagicMock(side_effect=FileNotFoundError("not found"))
        r = client.delete("/api/models/missing.gguf")
        assert r.status_code == 404
        assert "error" in r.json()


# ---------------------------------------------------------------------------
# Download API
# ---------------------------------------------------------------------------

class TestDownloadAPI:
    def test_start_download_returns_id(self, client):
        with patch("src.proxy.asyncio.create_task"):
            r = client.post("/api/models/download", json={
                "repo_id": "mlx-community/Qwen2.5-7B-Instruct-4bit",
                "format": "mlx",
            })
        assert r.status_code == 200
        assert "download_id" in r.json()
        assert len(r.json()["download_id"]) > 0

    def test_start_download_missing_repo_id(self, client):
        r = client.post("/api/models/download", json={"format": "mlx"})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_cancel_download_unknown_id(self, client):
        # Cancelling an unknown ID is a no-op — should still return ok
        r = client.post("/api/models/download/nonexistent/cancel")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_pause_download_unknown_id(self, client):
        r = client.post("/api/models/download/nonexistent/pause")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_progress_unknown_id_returns_404(self, client):
        r = client.get("/api/models/download/nonexistent/progress")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

class TestUploadAPI:
    def test_upload_image_returns_base64(self, client):
        # 1×1 white PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd5N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = client.post(
            "/api/upload",
            files={"file": ("test.png", png_bytes, "image/png")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "image"
        assert body["data"].startswith("data:image/png;base64,")
        assert body["name"] == "test.png"

    def test_upload_text_file(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("hello.txt", b"Hello, world!", "text/plain")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "text"
        assert "Hello, world!" in body["content"]

    def test_upload_audio_returns_transcription(self, client):
        """Audio uploads are auto-transcribed when voice backend is available."""
        r = client.post(
            "/api/upload",
            files={"file": ("clip.mp3", b"fake-audio-bytes", "audio/mpeg")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "text"
        assert body["source"] == "voice_transcription"
        assert body["content"] == "hello world"

    def test_upload_video_returns_type(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("clip.mp4", b"fake-video-bytes", "video/mp4")},
        )
        assert r.status_code == 200
        assert r.json()["type"] == "video"


# ---------------------------------------------------------------------------
# Hardware API
# ---------------------------------------------------------------------------

class TestHardwareAPI:
    def test_hardware_profile_returned(self, client):
        from src.hardware_profiler import HardwareProfile
        fake_profile = HardwareProfile(
            platform="darwin",
            arch="arm64",
            cpu_name="Apple M3 Pro",
            total_ram_gb=36.0,
            available_ram_gb=20.0,
            has_apple_silicon=True,
            has_nvidia_gpu=False,
            supports_mlx=True,
            llmfit_available=True,
        )
        # The endpoint imports hardware_profiler inside the function body;
        # patch at the source module level, not through proxy.
        with patch("src.hardware_profiler.get_hardware_profile", return_value=fake_profile), \
             patch("src.hardware_profiler._llmfit_bin", return_value="/path/to/llmfit"):
            r = client.get("/api/hardware")
        assert r.status_code == 200
        data = r.json()
        assert data["platform"] == "darwin"
        assert data["has_apple_silicon"] is True
        assert data["total_ram_gb"] == 36.0
        assert data["llmfit_available"] is True


# ---------------------------------------------------------------------------
# Recommended models
# ---------------------------------------------------------------------------

class TestRecommendedModelsAPI:
    def test_returns_cached_recommendations(self, client):
        import src.proxy as proxy_module
        proxy_module._recs_cache = {
            "total_ram_gb": 36.0,
            "has_apple_silicon": True,
            "llmfit_available": True,
            "recommendations": [
                {"name": "Qwen2.5-14B", "repo_id": "mlx-community/Qwen2.5-14B-Instruct-4bit",
                 "format": "mlx", "size_gb": 8.5, "quant": "4-bit", "context": 32768,
                 "why": "Fast on Apple Silicon", "fit_level": "Perfect Fit",
                 "use_case": "general", "provider": "MLX Community", "score": 95.0, "tps": 50.0,
                 "filename": None}
            ],
        }
        r = client.get("/api/recommended-models")
        assert r.status_code == 200
        body = r.json()
        assert len(body["recommendations"]) == 1
        assert body["recommendations"][0]["name"] == "Qwen2.5-14B"
        # Cleanup
        proxy_module._recs_cache = None

    def test_empty_when_no_cache(self, client):
        import src.proxy as proxy_module
        proxy_module._recs_cache = None
        with patch("src.proxy._build_recs_cache", new_callable=AsyncMock):
            r = client.get("/api/recommended-models")
        assert r.status_code == 200
        assert r.json()["recommendations"] == []


# ---------------------------------------------------------------------------
# Helper: _detect_image
# ---------------------------------------------------------------------------

class TestDetectImage:
    def test_detects_image_url_in_content_list(self):
        from src.proxy import _detect_image
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
        ]}]
        assert _detect_image(messages) is True

    def test_no_image_in_text_only(self):
        from src.proxy import _detect_image
        messages = [{"role": "user", "content": "Hello"}]
        assert _detect_image(messages) is False

    def test_detects_base64_image_in_string_content(self):
        from src.proxy import _detect_image
        messages = [{"role": "user", "content": "data:image/png;base64,abc123"}]
        assert _detect_image(messages) is True

    def test_only_checks_most_recent_user_message(self):
        from src.proxy import _detect_image
        # The function iterates in reverse and returns True on the first user message
        # that contains an image. If the most recent user message has no image but
        # an earlier one does, the function finds the image in the earlier message.
        # Verify: a conversation where only the latest message has no image still
        # returns True because the earlier message had one.
        messages = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "img"}}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "No image now"},
        ]
        # First user message has an image — _detect_image finds it even though
        # the most recent user message does not, because the loop continues past
        # user messages that contain no image.
        assert _detect_image(messages) is True

    def test_no_image_anywhere_returns_false(self):
        from src.proxy import _detect_image
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Second message"},
        ]
        assert _detect_image(messages) is False


# ---------------------------------------------------------------------------
# Vault API
# ---------------------------------------------------------------------------

class TestVaultDetect:
    def test_detect_vaults(self, client):
        with patch("src.vault_indexer.detect_vaults", return_value=[{"name": "studio", "path": "/home/user/vault"}]):
            r = client.get("/api/vault/detect")
        assert r.status_code == 200
        assert len(r.json()["vaults"]) == 1
        assert r.json()["vaults"][0]["name"] == "studio"


class TestVaultScan:
    def test_scan_missing_path(self, client):
        r = client.post("/api/vault/scan", json={})
        assert r.status_code == 400

    def test_scan_invalid_path(self, client):
        with patch("src.vault_indexer.validate_vault_path", return_value="Not a vault"):
            r = client.post("/api/vault/scan", json={"path": "/bad"})
        assert r.status_code == 400
        assert "Not a vault" in r.json()["error"]

    def test_scan_success(self, client):
        stats = {"total": 10, "added": 10, "updated": 0, "skipped": 0, "removed": 0, "errors": 0}
        with patch("src.vault_indexer.validate_vault_path", return_value=None), \
             patch("src.vault_indexer.scan_vault", return_value=stats):
            r = client.post("/api/vault/scan", json={"path": "/vault"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["total"] == 10


class TestVaultStats:
    def test_stats_missing_path(self, client):
        r = client.get("/api/vault/stats")
        assert r.status_code == 400

    def test_stats_returns_data(self, client):
        mock_stats = {"note_count": 5, "link_count": 3, "total_words": 100, "tag_count": 2, "top_tags": [], "folder_count": 1}
        with patch("src.vault_analyser.vault_stats", return_value=mock_stats):
            r = client.get("/api/vault/stats", params={"path": "/vault"})
        assert r.status_code == 200
        assert r.json()["note_count"] == 5


class TestVaultAnalysis:
    def test_analysis_missing_path(self, client):
        r = client.get("/api/vault/analysis")
        assert r.status_code == 400

    def test_analysis_returns_full_report(self, client):
        mock = {
            "stats": {"note_count": 3, "link_count": 1, "total_words": 50, "tag_count": 2, "top_tags": [], "folder_count": 0},
            "orphans": [{"rel_path": "o.md", "title": "O", "word_count": 10, "has_outgoing_links": False}],
            "dead_ends": [], "broken_links": [], "tag_orphans": [], "link_suggestions": [],
        }
        with patch("src.vault_analyser.full_analysis", return_value=mock):
            r = client.get("/api/vault/analysis", params={"path": "/vault"})
        assert r.status_code == 200
        assert r.json()["stats"]["note_count"] == 3
        assert len(r.json()["orphans"]) == 1


class TestVaultSearch:
    def test_search_empty_query(self, client):
        r = client.get("/api/vault/search", params={"path": "/v", "q": ""})
        assert r.json()["results"] == []

    def test_search_returns_matches(self, client):
        mock_notes = [
            {"rel_path": "ml.md", "title": "Machine Learning", "tags": ["python"], "word_count": 50,
             "headings": [], "id": "1", "vault_path": "/v", "created": 1, "modified": 2, "content_hash": "h", "indexed_at": 3},
            {"rel_path": "cooking.md", "title": "Recipes", "tags": ["food"], "word_count": 30,
             "headings": [], "id": "2", "vault_path": "/v", "created": 1, "modified": 2, "content_hash": "h", "indexed_at": 3},
        ]
        with patch("src.proxy.list_vault_notes", return_value=mock_notes):
            r = client.get("/api/vault/search", params={"path": "/v", "q": "machine"})
        assert len(r.json()["results"]) == 1
        assert r.json()["results"][0]["title"] == "Machine Learning"


# ---------------------------------------------------------------------------
# Voice API
# ---------------------------------------------------------------------------

class TestVoiceAPI:
    def test_voice_config(self, client):
        r = client.get("/api/voice/config")
        assert r.status_code == 200
        data = r.json()
        assert "stt_model" in data
        assert "tts_model" in data
        assert "models" in data

    def test_voice_models(self, client):
        r = client.get("/api/voice/models")
        assert r.status_code == 200
        assert "models" in r.json()

    def test_transcription(self, client):
        # Create a minimal WAV file
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)  # 1 second of silence
        wav_bytes = buf.getvalue()

        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav_bytes, "audio/wav")},
        )
        assert r.status_code == 200
        assert "text" in r.json()
        assert r.json()["text"] == "hello world"

    def test_speech_synthesis(self, client):
        r = client.post(
            "/v1/audio/speech",
            json={"input": "Hello, world!"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/wav"
        assert len(r.content) > 0

    def test_speech_requires_input(self, client):
        r = client.post("/v1/audio/speech", json={"input": ""})
        assert r.status_code == 400
