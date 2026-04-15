"""
Obsidian vault indexer — scans markdown files and stores structured data in SQLite.

Read-only access to the vault: only open(path, 'r') is ever used.
No writes, renames, deletes, or file watchers touch the vault.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import time
import uuid
from pathlib import Path

from . import store

logger = logging.getLogger(__name__)

# ── Obsidian auto-detection ──────────────────────────────────────────────────


def _obsidian_config_path() -> Path | None:
    """Return the path to obsidian.json, or None if not found."""
    system = platform.system()
    if system == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    elif system == "Linux":
        p = Path.home() / ".config" / "obsidian" / "obsidian.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return None
        p = Path(appdata) / "obsidian" / "obsidian.json"
    else:
        return None
    if p and p.is_file():
        return p
    return None


def detect_vaults() -> list[dict]:
    """Auto-detect Obsidian vaults from obsidian.json.

    Returns a list of dicts: [{"name": "...", "path": "..."}]
    """
    config_path = _obsidian_config_path()
    if not config_path:
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        vaults = []
        for _id, info in data.get("vaults", {}).items():
            vpath = info.get("path", "")
            if vpath and Path(vpath).is_dir():
                name = Path(vpath).name
                vaults.append({"name": name, "path": vpath})
        return vaults
    except Exception as e:
        logger.warning(f"Could not read Obsidian config: {e}")
        return []


def validate_vault_path(path: str) -> str | None:
    """Validate that path is a genuine Obsidian vault.

    Returns an error message, or None if valid.
    """
    p = Path(path).resolve()

    # Must exist
    if not p.is_dir():
        return "Path does not exist or is not a directory."

    # Must be under user home (prevent scanning /etc, /usr, etc.)
    try:
        p.relative_to(Path.home())
    except ValueError:
        return "Vault path must be inside your home directory."

    # Must contain .obsidian/ folder
    if not (p / ".obsidian").is_dir():
        return "Not an Obsidian vault (no .obsidian/ folder found)."

    return None


# ── Markdown parsing (read-only) ────────────────────────────────────────────

_WIKI_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TAG_INLINE = re.compile(r"(?:^|\s)#([a-zA-Z][\w/-]*)", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_DAILY_NOTE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TASK = re.compile(r"^- \[([ x])\] (.+)$", re.MULTILINE)

# Frontmatter YAML tag patterns (simple extraction without a YAML library)
_FM_TAGS = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)
_FM_TAGS_LIST = re.compile(r"^tags:\s*$", re.MULTILINE)
_FM_TAG_ITEM = re.compile(r"^\s*-\s+(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata_dict, body_without_frontmatter).

    metadata_dict contains:
      - "tags": list[str]  (already-handled tag fields)
      - "properties": dict  (all other scalar key-value pairs)
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    meta: dict = {}

    # Extract tags from frontmatter
    tags: list[str] = []
    m = _FM_TAGS.search(fm_block)
    if m:
        tags = [t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()]
    else:
        # List-style tags
        m2 = _FM_TAGS_LIST.search(fm_block)
        if m2:
            after_tags = fm_block[m2.end():]
            for line in after_tags.split("\n"):
                tm = _FM_TAG_ITEM.match(line)
                if tm:
                    tags.append(tm.group(1).strip().strip("'\""))
                elif line.strip() and not line.startswith(" "):
                    break
    if tags:
        meta["tags"] = tags

    # Extract other scalar key-value properties (skip tags key)
    properties: dict[str, str] = {}
    for line in fm_block.splitlines():
        kv = line.split(":", 1)
        if len(kv) != 2:
            continue
        key = kv[0].strip()
        val = kv[1].strip()
        if not key or key == "tags" or val.startswith("[") or val.startswith("-"):
            continue
        properties[key] = val.strip("'\"")
    if properties:
        meta["properties"] = properties

    return meta, body


def parse_note(rel_path: str, text: str) -> dict:
    """Parse a markdown note into structured data. Pure function, no I/O."""
    meta, body = _parse_frontmatter(text)

    # Title: first H1, or filename
    title_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else Path(rel_path).stem

    # Tags: frontmatter + inline
    tags = list(meta.get("tags", []))
    inline_tags = _TAG_INLINE.findall(body)
    tags.extend(t for t in inline_tags if t not in tags)

    # Headings
    headings = [
        {"level": len(m.group(1)), "text": m.group(2).strip()}
        for m in _HEADING.finditer(body)
    ]

    # Links
    wiki_links = [
        {"to_note": m.group(1).strip(), "link_type": "wiki"}
        for m in _WIKI_LINK.finditer(body)
    ]
    md_links = [
        {"to_note": m.group(2).strip(), "link_type": "markdown"}
        for m in _MD_LINK.finditer(body)
        if not m.group(2).startswith("http")  # skip external URLs
    ]
    links = wiki_links + md_links

    # Word count (body only, no frontmatter)
    word_count = len(body.split())

    # Content hash for change detection
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Daily note detection — filename stem matches YYYY-MM-DD
    stem = Path(rel_path).stem
    is_daily_note = bool(_DAILY_NOTE.fullmatch(stem))

    # Task extraction — "- [ ] ..." and "- [x] ..."
    tasks = []
    for line_no, line in enumerate(text.splitlines(), 1):
        tm = _TASK.match(line)
        if tm:
            tasks.append({
                "text": tm.group(2).strip(),
                "completed": tm.group(1) == "x",
                "line": line_no,
            })

    # Frontmatter properties (non-tag key-values)
    properties: dict[str, str] = meta.get("properties", {})

    # Body snippet — first 500 chars, stripped of frontmatter
    body_snippet = body[:500]

    return {
        "title": title,
        "tags": tags,
        "headings": headings,
        "links": links,
        "word_count": word_count,
        "content_hash": content_hash,
        "is_daily_note": is_daily_note,
        "tasks": tasks,
        "properties": properties,
        "body_snippet": body_snippet,
    }


# ── Scanning ─────────────────────────────────────────────────────────────────


def scan_vault(vault_path: str) -> dict:
    """Scan an Obsidian vault and index all markdown files.

    Returns a summary dict: {total, added, updated, removed, skipped, errors}
    """
    vpath = Path(vault_path).resolve()
    vault_key = str(vpath)

    err = validate_vault_path(vault_key)
    if err:
        raise ValueError(err)

    # Collect all .md files (skip .obsidian/, .trash/, .icloud placeholders)
    md_files: list[Path] = []
    for root, dirs, files in os.walk(vpath):
        # Skip hidden/internal directories
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in (".obsidian", ".trash")
        ]
        for fname in files:
            if fname.endswith(".md") and not fname.startswith("."):
                full = Path(root) / fname
                # Skip iCloud evicted files (placeholders)
                if fname.endswith(".icloud"):
                    continue
                # Reject symlinks that resolve outside the vault
                if full.is_symlink():
                    try:
                        resolved = full.resolve()
                        resolved.relative_to(vpath)
                    except (ValueError, OSError):
                        continue
                md_files.append(full)

    # Track which rel_paths we've seen (for removal detection)
    seen_rel_paths: set[str] = set()
    stats = {"total": len(md_files), "added": 0, "updated": 0, "skipped": 0, "removed": 0, "errors": 0}

    for fpath in md_files:
        rel_path = str(fpath.relative_to(vpath))
        seen_rel_paths.add(rel_path)

        try:
            # Read file (read-only)
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()

            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            # Check if note has changed since last index
            existing_hash = store.get_vault_note_content_hash(vault_key, rel_path)
            if existing_hash == content_hash:
                stats["skipped"] += 1
                continue

            # Parse
            parsed = parse_note(rel_path, text)

            # File timestamps
            stat = fpath.stat()
            created = getattr(stat, "st_birthtime", stat.st_ctime)
            modified = stat.st_mtime

            # Upsert note
            note = {
                "id": str(uuid.uuid4()),
                "vault_path": vault_key,
                "rel_path": rel_path,
                "title": parsed["title"],
                "word_count": parsed["word_count"],
                "tags": parsed["tags"],
                "headings": parsed["headings"],
                "created": created,
                "modified": modified,
                "content_hash": parsed["content_hash"],
                "indexed_at": time.time(),
                "is_daily_note": parsed["is_daily_note"],
                "tasks": parsed["tasks"],
                "properties": parsed["properties"],
                "body_snippet": parsed["body_snippet"],
            }
            store.upsert_vault_note(note)
            store.replace_vault_links(vault_key, rel_path, parsed["links"])

            if existing_hash is None:
                stats["added"] += 1
            else:
                stats["updated"] += 1

        except Exception as e:
            logger.warning(f"Error indexing {rel_path}: {e}")
            stats["errors"] += 1

    # Remove notes that no longer exist on disk
    existing_notes = store.list_vault_notes(vault_key)
    for note in existing_notes:
        if note["rel_path"] not in seen_rel_paths:
            store.delete_vault_note(vault_key, note["rel_path"])
            stats["removed"] += 1

    logger.info(
        f"Vault scan complete: {stats['total']} files, "
        f"{stats['added']} added, {stats['updated']} updated, "
        f"{stats['skipped']} unchanged, {stats['removed']} removed, "
        f"{stats['errors']} errors"
    )
    return stats
