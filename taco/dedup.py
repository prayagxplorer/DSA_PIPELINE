"""
Two-layer dedup, run in this order because each layer is progressively more
expensive:

  1. URL exact match -- free, instant. TACO problems carry a source URL;
     if two rows share one, they're unambiguously the same original problem
     (this catches the "APPS and CodeContests both scraped the same
     Codeforces problem" case directly, since the URL survives regardless
     of which curator's pipeline processed it).

  2. Embedding cosine similarity -- catches paraphrased/reworded duplicates
     that don't share a URL, at real (but small, local) compute cost.

The registry (previously-served problems) is a local JSON file for now.
This is a STUB standing in for a real pgvector query against Supabase --
swap `LocalRegistry` for a Supabase-backed version once that's wired up,
the interface (has_duplicate / register) stays the same either way.
"""

import json
import os
from typing import Optional

from embeddings import get_embedding, cosine_similarity

SIMILARITY_THRESHOLD = 0.90  # cosine distance cutoff -- tune once you have real data to check against


class LocalRegistry:
    """
    Stand-in for the real Supabase/pgvector-backed duplicate registry.
    Persists to a JSON file so dedup works across separate runs, not just
    within a single script execution.
    """

    def __init__(self, path: str = "problem_registry.json", fresh_start: bool = False):
        self.path = path
        self.entries: list[dict] = []
        if fresh_start and os.path.exists(path):
            os.remove(path)
        if os.path.exists(path):
            with open(path) as f:
                self.entries = json.load(f)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.entries, f)

    def has_url(self, url: Optional[str]) -> bool:
        if not url:
            return False
        return any(e.get("source_url") == url for e in self.entries)

    def most_similar(self, embedding) -> tuple[Optional[dict], float]:
        best_entry, best_score = None, 0.0
        for entry in self.entries:
            score = cosine_similarity(embedding, entry["embedding"])
            if score > best_score:
                best_entry, best_score = entry, score
        return best_entry, best_score

    def register(self, source_url: Optional[str], title: str, embedding):
        self.entries.append({
            "source_url": source_url,
            "title": title,
            "embedding": [float(x) for x in embedding],
        })


def check_duplicate(registry: LocalRegistry, title: str, description: str, source_url: Optional[str]) -> dict:
    """
    Returns {"is_duplicate": bool, "reason": str, "matched": dict|None}
    Checks URL first (cheap), only computes an embedding if that passes.
    """
    if registry.has_url(source_url):
        return {"is_duplicate": True, "reason": "exact url match", "matched": {"source_url": source_url}}

    embedding = get_embedding(f"{title}\n{description}")
    match, score = registry.most_similar(embedding)
    if match and score >= SIMILARITY_THRESHOLD:
        return {"is_duplicate": True, "reason": f"embedding similarity {score:.3f}", "matched": match}

    return {"is_duplicate": False, "reason": None, "matched": None, "_embedding": embedding}
