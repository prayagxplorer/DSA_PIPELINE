"""
Provides text embeddings for dedup similarity checks.

DELIBERATE DESIGN CHOICE: uses a local sentence-transformers model, not
Gemini/Groq's API. Dedup checks run on every candidate problem, which adds
up fast -- running that through your paid-quota LLM providers would eat into
the same daily caps you're trying to protect for generation/review/fixing.
Embeddings computed locally are free, unlimited, and don't touch any
provider's rate limit at all.

Primary path: sentence-transformers (`all-MiniLM-L6-v2`, ~80MB, downloads
once then runs fully offline). Install with:
    pip install sentence-transformers

Fallback path: a crude hashing-based bag-of-words vectorizer, used
automatically if sentence-transformers isn't installed or its model can't be
downloaded (e.g. no internet access in this environment). This fallback is
NOT good enough for real duplicate detection -- it's here only so the rest
of the pipeline is runnable/testable without the ML dependency. Install the
real thing before running this against actual TACO data.
"""

import hashlib
import re
import numpy as np

_model = None
_using_fallback = False


def _try_load_sentence_transformer():
    global _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        return True
    except Exception as e:
        print(f"[embeddings] sentence-transformers unavailable ({e}); "
              f"falling back to crude hashing vectorizer. Install "
              f"sentence-transformers for real dedup quality.")
        return False


def _fallback_embed(text: str, dims: int = 256) -> np.ndarray:
    """
    Crude, dependency-free embedding: hashes word tokens into a fixed-size
    vector. Captures rough vocabulary overlap, nothing about meaning or
    word order. FOR TESTING/DEMO ONLY -- do not rely on this for real
    duplicate detection.
    """
    vec = np.zeros(dims, dtype=np.float32)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for tok in tokens:
        idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def get_embedding(text: str) -> np.ndarray:
    global _model, _using_fallback
    if _model is None and not _using_fallback:
        if not _try_load_sentence_transformer():
            _using_fallback = True

    if _model is not None:
        return _model.encode(text, normalize_embeddings=True)
    return _fallback_embed(text)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a), np.asarray(b)
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
