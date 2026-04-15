"""
TF-IDF semantic search for indexed Obsidian vaults.

Uses sklearn TfidfVectorizer + cosine similarity over title + tags + body_snippet.
The index is built lazily on first search and cached per vault_path.
Call clear_vault_search_cache(vault_path) after a re-scan to invalidate.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import store

if TYPE_CHECKING:
    from scipy.sparse import csr_matrix
    from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)

# Module-level cache: vault_path -> (vectorizer, matrix, notes)
_cache: dict[str, tuple["TfidfVectorizer", "csr_matrix", list[dict]]] = {}


def clear_vault_search_cache(vault_path: str) -> None:
    """Invalidate the TF-IDF index for a vault (call after re-scan)."""
    _cache.pop(vault_path, None)


def build_tfidf_index(vault_path: str) -> "tuple[TfidfVectorizer, csr_matrix, list[dict]]":
    """Load all notes for vault_path from DB, build TF-IDF index over title+tags+body_snippet."""
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import]

    notes = store.list_vault_notes(vault_path)
    if not notes:
        from scipy.sparse import csr_matrix  # type: ignore[import]
        vec: TfidfVectorizer = TfidfVectorizer()
        vec.fit([""])
        return vec, csr_matrix((0, 0)), []

    docs = []
    for n in notes:
        tags_str = " ".join(n["tags"]) if isinstance(n["tags"], list) else ""
        snippet = n.get("body_snippet") or ""
        docs.append(f"{n['title']} {tags_str} {snippet}")

    vectorizer: TfidfVectorizer = TfidfVectorizer(
        strip_accents="unicode",
        analyzer="word",
        min_df=1,
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(docs)
    return vectorizer, matrix, notes


def semantic_search(vault_path: str, query: str, limit: int = 20) -> list[dict]:
    """Return top-K notes by TF-IDF cosine similarity to query.

    Each result: {rel_path, title, score, snippet, tags, is_daily_note, tasks_count}
    """
    from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import]

    if not query.strip():
        return []

    if vault_path not in _cache:
        try:
            _cache[vault_path] = build_tfidf_index(vault_path)
        except Exception as exc:
            logger.warning("Failed to build TF-IDF index for %s: %s", vault_path, exc)
            return []

    vectorizer, matrix, notes = _cache[vault_path]
    if not notes or matrix.shape[0] == 0:
        return []

    try:
        q_vec = vectorizer.transform([query])
        sims = cosine_similarity(q_vec, matrix).flatten()
    except Exception as exc:
        logger.warning("TF-IDF transform failed: %s", exc)
        return []

    top_indices = sims.argsort()[::-1][:limit]
    results = []
    for idx in top_indices:
        score = float(sims[idx])
        if score <= 0.0:
            break
        n = notes[idx]
        results.append({
            "rel_path": n["rel_path"],
            "title": n["title"],
            "score": round(score, 4),
            "snippet": (n.get("body_snippet") or "")[:200],
            "tags": n["tags"] if isinstance(n["tags"], list) else [],
            "is_daily_note": bool(n.get("is_daily_note")),
            "tasks_count": len(n.get("tasks") or []),
        })
    return results
