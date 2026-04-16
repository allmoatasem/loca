from __future__ import annotations

import base64
import logging
from pathlib import Path

from ..base import BaseAdapter, Chunk

logger = logging.getLogger(__name__)
_SUPPORTED = {".png", ".jpg", ".jpeg", ".webp"}


class ImageAdapter(BaseAdapter):
    source_name = "image"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _SUPPORTED

    def extract(self, path: Path) -> list[Chunk]:
        """OCR via local vision model. Returns empty list if unavailable."""
        try:
            import httpx  # noqa: PLC0415

            with open(path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            suffix = path.suffix.lower().lstrip(".")
            mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"

            resp = httpx.post(
                "http://localhost:8000/v1/chat/completions",
                json={
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image in detail, including any text visible."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                        ],
                    }],
                    "stream": False,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            if not text.strip():
                return []
            return [Chunk(
                text=text.strip(),
                source="image",
                title=path.name,
                created_at="",
                metadata={"path": str(path)},
            )]
        except Exception as exc:
            logger.debug("ImageAdapter skipping %s: %s", path.name, exc)
            return []
