"""
Vault analyser — structural analysis of an indexed Obsidian vault.

All analysis is performed on the SQLite index, not the vault files.
No I/O to the vault directory occurs here.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from . import store

logger = logging.getLogger(__name__)


def vault_stats(vault_path: str) -> dict:
    """Return high-level stats for an indexed vault."""
    notes = store.list_vault_notes(vault_path)
    links = store.list_vault_links(vault_path)

    if not notes:
        return {
            "note_count": 0,
            "link_count": 0,
            "total_words": 0,
            "tag_count": 0,
            "top_tags": [],
            "folder_count": 0,
            "daily_note_count": 0,
            "open_tasks": 0,
            "done_tasks": 0,
        }

    all_tags: list[str] = []
    folders: set[str] = set()
    total_words = 0
    daily_note_count = 0
    open_tasks = 0
    done_tasks = 0

    for n in notes:
        tags = n["tags"] if isinstance(n["tags"], list) else json.loads(n["tags"])
        all_tags.extend(tags)
        total_words += n["word_count"]
        parent = str(Path(n["rel_path"]).parent)
        if parent != ".":
            folders.add(parent)
        if n.get("is_daily_note"):
            daily_note_count += 1
        for t in (n.get("tasks") or []):
            if t.get("completed"):
                done_tasks += 1
            else:
                open_tasks += 1

    tag_counts = Counter(all_tags).most_common(20)

    return {
        "note_count": len(notes),
        "link_count": len(links),
        "total_words": total_words,
        "tag_count": len(set(all_tags)),
        "top_tags": [{"tag": t, "count": c} for t, c in tag_counts],
        "folder_count": len(folders),
        "daily_note_count": daily_note_count,
        "open_tasks": open_tasks,
        "done_tasks": done_tasks,
    }


def find_orphan_notes(vault_path: str) -> list[dict]:
    """Find notes with no incoming links (orphans)."""
    notes = store.list_vault_notes(vault_path)
    links = store.list_vault_links(vault_path)

    linked_to: set[str] = set()
    for lnk in links:
        target = lnk["to_note"]
        linked_to.add(target)
        linked_to.add(target + ".md")
        linked_to.add(Path(target).stem)

    links_from: set[str] = {lnk["from_note"] for lnk in links}

    orphans = []
    for note in notes:
        rel = note["rel_path"]
        stem = Path(rel).stem

        is_linked = (
            rel in linked_to
            or stem in linked_to
            or stem.replace(" ", "-") in linked_to
        )

        if not is_linked:
            orphans.append({
                "rel_path": rel,
                "title": note["title"],
                "word_count": note["word_count"],
                "has_outgoing_links": rel in links_from,
            })

    return orphans


def find_dead_ends(vault_path: str) -> list[dict]:
    """Find notes with no outgoing links (dead ends)."""
    notes = store.list_vault_notes(vault_path)
    links = store.list_vault_links(vault_path)

    has_outgoing: set[str] = {lnk["from_note"] for lnk in links}

    return [
        {"rel_path": n["rel_path"], "title": n["title"], "word_count": n["word_count"]}
        for n in notes
        if n["rel_path"] not in has_outgoing
    ]


def find_broken_links(vault_path: str) -> list[dict]:
    """Find links that point to notes that don't exist in the vault."""
    notes = store.list_vault_notes(vault_path)
    links = store.list_vault_links(vault_path)

    known: set[str] = set()
    for n in notes:
        rel = n["rel_path"]
        known.add(rel)
        known.add(Path(rel).stem)
        known.add(Path(rel).stem.replace(" ", "-"))
        if rel.endswith(".md"):
            known.add(rel[:-3])

    broken = []
    for lnk in links:
        target = lnk["to_note"]
        target_stem = Path(target).stem if "/" in target or "." in target else target

        is_known = (
            target in known
            or target_stem in known
            or target.replace(" ", "-") in known
            or target_stem.replace(" ", "-") in known
        )

        if not is_known:
            broken.append({
                "from_note": lnk["from_note"],
                "to_note": target,
                "link_type": lnk["link_type"],
            })

    return broken


def find_tag_orphans(vault_path: str) -> list[dict]:
    """Find tags used only once."""
    notes = store.list_vault_notes(vault_path)

    tag_notes: dict[str, list[str]] = {}
    for n in notes:
        tags = n["tags"] if isinstance(n["tags"], list) else json.loads(n["tags"])
        for t in tags:
            tag_notes.setdefault(t, []).append(n["rel_path"])

    return [
        {"tag": tag, "note": paths[0]}
        for tag, paths in sorted(tag_notes.items())
        if len(paths) == 1
    ]


def find_link_suggestions(vault_path: str, max_suggestions: int = 20) -> list[dict]:
    """Suggest links between notes that share tags but aren't linked."""
    notes = store.list_vault_notes(vault_path)
    links = store.list_vault_links(vault_path)

    existing: set[tuple[str, str]] = set()
    for lnk in links:
        existing.add((lnk["from_note"], lnk["to_note"]))

    tag_notes: dict[str, list[str]] = {}
    note_map: dict[str, dict] = {}
    for n in notes:
        rel = n["rel_path"]
        note_map[rel] = n
        tags = n["tags"] if isinstance(n["tags"], list) else json.loads(n["tags"])
        for t in tags:
            tag_notes.setdefault(t, []).append(rel)

    pair_scores: dict[tuple[str, str], int] = {}
    for tag, members in tag_notes.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = (min(a, b), max(a, b))
                stem_a = Path(a).stem
                stem_b = Path(b).stem
                if (a, b) in existing or (b, a) in existing:
                    continue
                if (a, stem_b) in existing or (b, stem_a) in existing:
                    continue
                pair_scores[pair] = pair_scores.get(pair, 0) + 1

    ranked = sorted(pair_scores.items(), key=lambda x: -x[1])[:max_suggestions]

    suggestions = []
    for (a, b), score in ranked:
        tags_a = set(note_map[a]["tags"] if isinstance(note_map[a]["tags"], list) else json.loads(note_map[a]["tags"]))
        tags_b = set(note_map[b]["tags"] if isinstance(note_map[b]["tags"], list) else json.loads(note_map[b]["tags"]))
        shared = sorted(tags_a & tags_b)

        suggestions.append({
            "note_a": {"rel_path": a, "title": note_map[a]["title"]},
            "note_b": {"rel_path": b, "title": note_map[b]["title"]},
            "shared_tags": shared,
            "score": score,
            "reason": f"Share {score} tag{'s' if score > 1 else ''}: {', '.join(shared[:3])}",
        })

    return suggestions


def full_analysis(vault_path: str) -> dict:
    """Run all analyses and return a combined report."""
    return {
        "stats": vault_stats(vault_path),
        "orphans": find_orphan_notes(vault_path),
        "dead_ends": find_dead_ends(vault_path),
        "broken_links": find_broken_links(vault_path),
        "tag_orphans": find_tag_orphans(vault_path),
        "link_suggestions": find_link_suggestions(vault_path),
    }
