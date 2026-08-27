from __future__ import annotations

import json
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", os.environ.get("SUPPORT_AGENT_HF_ENDPOINT", "https://hf-mirror.com"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from app.core.config import get_settings
from app.models.schemas import Category, KnowledgeHit
from app.services.chunker import chunk_article
from app.services.vector_store import get_vector_store
from app.services.reranker import rerank

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"refund", "logistics", "account", "product", "complaint", "invoice", "other"}


class KnowledgeBase:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with self._lock:
            self.articles = self._load_articles()
            self.vs = get_vector_store()
            if self.vs.count() == 0:
                self._rebuild_index()

    def _load_articles(self) -> list[dict]:
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _rebuild_index(self):
        all_chunks = []
        for article in self.articles:
            all_chunks.extend(chunk_article(article))
        if all_chunks:
            self.vs.build_index(all_chunks)

    def search(self, query: str, category: Category | None = None, limit: int = 3) -> list[KnowledgeHit]:
        with self._lock:
            raw_hits = self.vs.search(query, category=category, k=10)
        if not raw_hits:
            return []

        ranked = rerank(query, raw_hits, top_k=limit)
        results = []
        for hit in ranked:
            meta = hit.get("metadata", {})
            category = meta.get("category", "other")
            if category not in _VALID_CATEGORIES:
                category = "other"
            results.append(
                KnowledgeHit(
                    id=meta.get("doc_id", ""),
                    title=meta.get("title", ""),
                    category=category,
                    score=round(1.0 - hit.get("score", 0), 3),
                    answer=hit.get("text", ""),
                    retrieval_method="bge_m3_reranker",
                )
            )
        return results

    def rebuild(self):
        with self._lock:
            articles = self._load_articles()
            new_vs = get_vector_store()
            all_chunks = []
            for article in articles:
                all_chunks.extend(chunk_article(article))
            if all_chunks:
                new_vs.build_index(all_chunks)
                new_vs._save()
            self.articles = articles
            self.vs = new_vs


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase(get_settings().knowledge_base_path)
