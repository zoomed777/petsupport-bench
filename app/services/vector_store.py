from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", os.environ.get("SUPPORT_AGENT_HF_ENDPOINT", "https://hf-mirror.com"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-m3"

_embedder: SentenceTransformer | None = None
_embedder_lock = threading.Lock()


def _get_embedder() -> SentenceTransformer | None:
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder
        try:
            _embedder = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=True)
            logger.info("bge-m3 embedder loaded (dim=%d)", _embedder.get_sentence_embedding_dimension())
            return _embedder
        except Exception as exc:
            logger.warning("Failed to load bge-m3: %s", exc)
            return None


class VectorStore:
    """In-memory vector store using numpy + bge-m3 embeddings.

    Persistence is handled by saving/loading index to/from disk as .npy files.
    Designed to avoid native-library lock issues on Windows.
    """

    def __init__(self, persist_dir: str | Path | None = None):
        self.embedder = _get_embedder()
        if persist_dir is None:
            persist_dir = Path(get_settings().data_dir) / "chroma_db"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict] = []
        self.embeddings: np.ndarray | None = None

        self._load()
        logger.info("VectorStore initialized (%d docs)", len(self.ids))

    def build_index(self, chunks: list[dict]) -> int:
        if not self.embedder:
            logger.error("Embedder not available, cannot build index")
            return 0

        texts = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        ids = [_make_id(m) for m in metadatas]

        logger.info("Embedding %d chunks with bge-m3...", len(texts))
        embeddings = self.embedder.encode(texts, show_progress_bar=True, normalize_embeddings=True)

        self.ids = ids
        self.documents = texts
        self.metadatas = metadatas
        self.embeddings = np.array(embeddings, dtype=np.float32)

        self._save()
        logger.info("Index built: %d chunks", len(chunks))
        return len(chunks)

    def search(self, query: str, category: str | None = None, k: int = 10) -> list[dict]:
        if not self.embedder or self.embeddings is None or len(self.ids) == 0:
            return []

        query_vec = self.embedder.encode(query, normalize_embeddings=True)
        scores = np.dot(self.embeddings, query_vec)

        indices = np.argsort(scores)[::-1]

        hits = []
        for idx in indices:
            meta = self.metadatas[idx]
            if category and meta.get("category") != category:
                continue
            hits.append({
                "id": self.ids[idx],
                "text": self.documents[idx],
                "metadata": meta,
                "score": float(1.0 - scores[idx]),
            })
            if len(hits) >= k:
                break
        return hits

    def count(self) -> int:
        return len(self.ids)

    def _save(self):
        if self.embeddings is not None:
            np.save(self.persist_dir / "embeddings.npy", self.embeddings)
            import json
            meta = {"ids": self.ids, "documents": self.documents, "metadatas": self.metadatas}
            with open(self.persist_dir / "index.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

    def _load(self):
        import json
        emb_path = self.persist_dir / "embeddings.npy"
        meta_path = self.persist_dir / "index.json"
        if emb_path.exists() and meta_path.exists():
            try:
                self.embeddings = np.load(emb_path)
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.ids = meta["ids"]
                self.documents = meta["documents"]
                self.metadatas = meta["metadatas"]
                logger.info("Loaded %d docs from disk", len(self.ids))
            except Exception as exc:
                logger.warning("Failed to load existing index: %s", exc)


def _make_id(metadata: dict) -> str:
    doc_id = metadata.get("doc_id", "unknown")
    chunk_idx = metadata.get("chunk_index", 0)
    return f"{doc_id}_chunk{chunk_idx}"


@lru_cache
def get_vector_store() -> VectorStore:
    return VectorStore()
