from __future__ import annotations

import os
os.environ.setdefault("HF_ENDPOINT", os.environ.get("SUPPORT_AGENT_HF_ENDPOINT", "https://hf-mirror.com"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import logging
import threading
from functools import lru_cache

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

_reranker: CrossEncoder | None = None
_reranker_lock = threading.Lock()


def _get_reranker() -> CrossEncoder | None:
    global _reranker
    if _reranker is not None:
        return _reranker
    with _reranker_lock:
        if _reranker is not None:
            return _reranker
        try:
            _reranker = CrossEncoder(RERANKER_MODEL, trust_remote_code=True)
            logger.info("bge-reranker-v2-m3 loaded")
            return _reranker
        except Exception as exc:
            logger.warning("Failed to load reranker: %s", exc)
            return None


def rerank(query: str, candidates: list[dict], top_k: int = 3) -> list[dict]:
    model = _get_reranker()
    if not model or not candidates:
        for c in candidates[:top_k]:
            c.setdefault("rerank_score", c.get("score", 0.0))
        return candidates[:top_k]

    pairs = [[query, c["text"]] for c in candidates]
    try:
        scores = model.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        return ranked[:top_k]
    except Exception as exc:
        logger.warning("Reranker failed: %s, falling back to raw order", exc)
        for c in candidates[:top_k]:
            c.setdefault("rerank_score", c.get("score", 0.0))
        return candidates[:top_k]


@lru_cache
def reranker_available() -> bool:
    return _get_reranker() is not None
