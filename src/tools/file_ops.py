"""
File operations tool — read and write local files.
Paths are resolved relative to the process working directory.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_READ_CHARS = 32_000  # ~8k tokens


def file_read(path: str, max_chars: int = _MAX_READ_CHARS) -> dict:
    """
    Read the contents of a local file.

    Returns:
        {"path": str, "content": str, "error": str | None}
    """
    p = Path(path).expanduser().resolve()
    try:
        if not p.exists():
            return {"path": str(p), "content": "", "error": f"File not found: {p}"}
        if not p.is_file():
            return {"path": str(p), "content": "", "error": f"Not a file: {p}"}

        content = p.read_text(encoding="utf-8", errors="replace")
        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        result = {"path": str(p), "content": content, "error": None}
        if truncated:
            result["warning"] = f"File truncated to {max_chars} characters"
        return result

    except PermissionError:
        return {"path": str(p), "content": "", "error": f"Permission denied: {p}"}
    except Exception as e:
        logger.exception(f"file_read error for {path}")
        return {"path": str(p), "content": "", "error": str(e)}


def file_write(path: str, content: str, overwrite: bool = True) -> dict:
    """
    Write content to a local file. Creates parent directories if needed.

    Returns:
        {"path": str, "bytes_written": int, "error": str | None}
    """
    p = Path(path).expanduser().resolve()
    try:
        if p.exists() and not overwrite:
            return {
                "path": str(p),
                "bytes_written": 0,
                "error": f"File already exists and overwrite=False: {p}",
            }

        p.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        p.write_bytes(encoded)
        logger.info(f"file_write: wrote {len(encoded)} bytes to {p}")
        return {"path": str(p), "bytes_written": len(encoded), "error": None}

    except PermissionError:
        return {"path": str(p), "bytes_written": 0, "error": f"Permission denied: {p}"}
    except Exception as e:
        logger.exception(f"file_write error for {path}")
        return {"path": str(p), "bytes_written": 0, "error": str(e)}
