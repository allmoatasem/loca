"""
VoiceBackend — speech-to-text (STT) and text-to-speech (TTS) for Loca.

Uses mlx-whisper for transcription and mlx-audio (Kokoro) for synthesis.
These are *not* chat models — they convert between audio and text.
The brain is still the loaded LLM; voice mode just adds ears and a mouth.

Models are downloaded on first use via huggingface_hub and cached in
~/loca_models/voice/{stt,tts}/.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VoiceConfig:
    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    tts_model: str = "prince-canuma/Kokoro-82M"
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    auto_tts: bool = False
    models_dir: Path = field(default_factory=lambda: Path("~/loca_models/voice").expanduser())

    @classmethod
    def from_config(cls, config: dict) -> VoiceConfig:
        voice = config.get("voice", {})
        models_dir = Path(
            config.get("inference", {}).get("models_dir", "~/loca_models")
        ).expanduser() / "voice"
        return cls(
            stt_model=voice.get("stt_model", cls.stt_model),
            tts_model=voice.get("tts_model", cls.tts_model),
            tts_voice=voice.get("tts_voice", cls.tts_voice),
            tts_speed=voice.get("tts_speed", cls.tts_speed),
            auto_tts=voice.get("auto_tts", cls.auto_tts),
            models_dir=models_dir,
        )


@dataclass
class VoiceModelInfo:
    name: str
    repo_id: str
    model_type: str       # "stt" or "tts"
    downloaded: bool
    size_gb: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "repo_id": self.repo_id,
            "model_type": self.model_type,
            "downloaded": self.downloaded,
            "size_gb": round(self.size_gb, 2),
        }


class VoiceBackend:
    def __init__(self, config: dict) -> None:
        self.cfg = VoiceConfig.from_config(config)
        self.cfg.models_dir.mkdir(parents=True, exist_ok=True)
        self._stt_loaded = False
        self._tts_loaded = False
        self._tts_pipeline: object | None = None

    # ------------------------------------------------------------------
    # Model inventory
    # ------------------------------------------------------------------

    def list_voice_models(self) -> list[VoiceModelInfo]:
        """Return known voice models with download status."""
        models: list[VoiceModelInfo] = []

        # STT model
        stt_name = self.cfg.stt_model.split("/")[-1]
        stt_downloaded = self._is_model_cached(self.cfg.stt_model)
        models.append(VoiceModelInfo(
            name=stt_name,
            repo_id=self.cfg.stt_model,
            model_type="stt",
            downloaded=stt_downloaded,
        ))

        # TTS model
        tts_name = self.cfg.tts_model.split("/")[-1]
        tts_downloaded = self._is_model_cached(self.cfg.tts_model)
        models.append(VoiceModelInfo(
            name=tts_name,
            repo_id=self.cfg.tts_model,
            model_type="tts",
            downloaded=tts_downloaded,
        ))

        return models

    def get_voice_config(self) -> dict:
        """Return current voice configuration."""
        return {
            "stt_model": self.cfg.stt_model,
            "tts_model": self.cfg.tts_model,
            "tts_voice": self.cfg.tts_voice,
            "tts_speed": self.cfg.tts_speed,
            "auto_tts": self.cfg.auto_tts,
            "models": [m.to_dict() for m in self.list_voice_models()],
        }

    # ------------------------------------------------------------------
    # STT — Speech-to-Text (mlx-whisper)
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_data: bytes,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
    ) -> dict:
        """
        Transcribe audio bytes to text using mlx-whisper.

        Returns OpenAI-compatible response:
          {"text": "transcribed text", "language": "en", "duration": 5.2}
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._transcribe_sync,
            audio_data,
            language,
            prompt,
            response_format,
        )

    def _transcribe_sync(
        self,
        audio_data: bytes,
        language: str | None,
        prompt: str | None,
        response_format: str,
    ) -> dict:
        try:
            import mlx_whisper  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "mlx-whisper is not installed. Run: pip install mlx-whisper"
            )

        # Write audio to temp file — mlx_whisper expects a file path
        suffix = ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            kwargs: dict = {"path_or_hf_repo": self.cfg.stt_model}
            if language:
                kwargs["language"] = language
            if prompt:
                kwargs["initial_prompt"] = prompt

            result = mlx_whisper.transcribe(
                temp_path,
                **kwargs,
            )
            self._stt_loaded = True

            text = result.get("text", "").strip()

            if response_format == "verbose_json":
                return {
                    "text": text,
                    "language": result.get("language", ""),
                    "duration": result.get("duration", 0),
                    "segments": result.get("segments", []),
                }

            return {"text": text}
        finally:
            Path(temp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # TTS — Text-to-Speech (mlx-audio / Kokoro)
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float | None = None,
        response_format: str = "wav",
    ) -> bytes:
        """
        Synthesize text to audio bytes using mlx-audio (Kokoro).

        Returns raw audio bytes in the requested format.
        """
        import asyncio

        clean = _clean_for_tts(text)
        if not clean:
            raise RuntimeError("No speakable text after cleaning")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._synthesize_sync,
            clean,
            voice or self.cfg.tts_voice,
            speed if speed is not None else self.cfg.tts_speed,
            response_format,
        )

    def _synthesize_sync(
        self,
        text: str,
        voice: str,
        speed: float,
        response_format: str,
    ) -> bytes:
        try:
            from mlx_audio.tts.generate import generate_audio  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "mlx-audio is not installed. Run: pip install mlx-audio"
            )

        # generate_audio writes to disk — use a temp directory
        outdir = tempfile.mkdtemp()
        prefix = "loca_tts"

        try:
            generate_audio(
                text=text,
                model=self.cfg.tts_model,
                voice=voice,
                speed=speed,
                output_path=outdir,
                file_prefix=prefix,
                audio_format="wav",
                save=True,
                play=False,
                verbose=False,
            )

            self._tts_loaded = True

            # Find the generated file
            import glob
            wav_files = sorted(glob.glob(f"{outdir}/{prefix}*.wav"))
            if not wav_files:
                raise RuntimeError("TTS generated no audio output")

            # Read and return the WAV bytes
            wav_path = Path(wav_files[0])
            audio_bytes = wav_path.read_bytes()
            return audio_bytes
        finally:
            # Clean up temp directory
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_model_cached(repo_id: str) -> bool:
        """Check if a HuggingFace model is already cached locally."""
        try:
            from huggingface_hub import scan_cache_dir
            cache = scan_cache_dir()
            for repo in cache.repos:
                if repo.repo_id == repo_id:
                    return True
        except Exception:
            pass
        return False


def _clean_for_tts(text: str) -> str:
    """Strip markdown, code blocks, URLs, and other non-speakable content."""
    import re

    s = text

    # Remove code blocks (```...```)
    s = re.sub(r"```[\s\S]*?```", "", s)
    # Remove inline code (`...`)
    s = re.sub(r"`[^`]+`", "", s)
    # Remove markdown links [text](url) → text
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Remove bare URLs
    s = re.sub(r"https?://\S+", "", s)
    # Remove markdown bold/italic markers
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
    # Remove markdown headers (# ## ###)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    # Remove bullet/numbered list markers
    s = re.sub(r"^[\s]*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^[\s]*\d+\.\s+", "", s, flags=re.MULTILINE)
    # Remove horizontal rules
    s = re.sub(r"^[-*_]{3,}\s*$", "", s, flags=re.MULTILINE)
    # Collapse whitespace
    s = re.sub(r"\n{2,}", ". ", s)
    s = re.sub(r"\n", " ", s)
    s = re.sub(r"\s{2,}", " ", s)

    s = s.strip()

    # Truncate to ~500 chars for reasonable TTS duration
    if len(s) > 500:
        # Cut at last sentence boundary within limit
        cut = s[:500]
        for sep in [". ", "! ", "? "]:
            idx = cut.rfind(sep)
            if idx > 100:
                s = cut[: idx + 1]
                break
        else:
            s = cut + "…"

    return s
