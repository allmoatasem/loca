"""
CLI entry point for knowledge import.

Usage:
    python -m src.importers.cli <path>
    make import path=<path>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "[" + "░" * width + "]"
    filled = int(width * current / total)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


async def _run(path_str: str) -> int:
    """Return exit code: 0 = success, 1 = error."""
    from ..plugins.mempalace_plugin import MemPalaceMemoryPlugin
    from .service import build_default_service

    plugin = MemPalaceMemoryPlugin()
    if not plugin._available:
        print("✗ MemPalace is not available. Run: pip install mempalace 'chromadb>=1.5.4'",
              file=sys.stderr)
        return 1

    svc = build_default_service(plugin)

    display_name = path_str if path_str.startswith(("http://", "https://")) else Path(path_str).name

    async for event in svc.run(path_str):
        status = event.get("status")
        if status == "detecting":
            print(f"▶ Detecting format for: {display_name}")
        elif status == "extracting":
            print(f"  Adapter: {event['adapter']}  ({event['total']} chunks)")
        elif status == "progress":
            bar = _progress_bar(event["current"], event["total"])
            print(f"\r  Extracting... {bar} {event['current']}/{event['total']}",
                  end="", flush=True)
        elif status == "done":
            print(f"\n✓ Done — {event['stored']} stored, {event['skipped']} skipped (duplicates)")
            return 0
        elif status == "error":
            print(f"\n✗ Error: {event['message']}", file=sys.stderr)
            return 1
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.importers.cli <path-or-url>", file=sys.stderr)
        sys.exit(1)
    arg = sys.argv[1]
    if not (arg.startswith("http://") or arg.startswith("https://")):
        resolved = Path(arg).expanduser().resolve()
        if not resolved.exists():
            print(f"✗ Path not found: {resolved}", file=sys.stderr)
            sys.exit(1)
        arg = str(resolved)
    exit_code = asyncio.run(_run(arg))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
