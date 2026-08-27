from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        boundary = _find_sentence_boundary(text, end)
        if boundary == end:
            boundary = _find_paragraph_boundary(text, end)
        chunks.append(text[start:boundary])
        start = boundary - overlap if boundary - overlap > start else boundary

    return chunks


def _find_sentence_boundary(text: str, pos: int) -> int:
    for c in ("。", "！", "？", "\n", ".", "!", "?"):
        idx = text.rfind(c, pos - 40, pos + 10)
        if idx >= 0:
            return idx + 1
    return pos


def _find_paragraph_boundary(text: str, pos: int) -> int:
    idx = text.rfind("\n", pos - 40, pos)
    if idx >= 0:
        return idx + 1
    return pos


def chunk_article(article: dict, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    body = article.get("answer") or article.get("content", "")
    full_text = " ".join(
        [
            article.get("title", ""),
            body,
            " ".join(article.get("keywords") or []),
            article.get("category", ""),
        ]
    )

    segments = split_text(full_text, chunk_size, overlap)
    chunks = []
    for i, seg in enumerate(segments):
        chunks.append({
            "text": seg,
            "metadata": {
                "doc_id": article.get("policy_id") or article.get("id", f"doc_{i}"),
                "title": article.get("title", ""),
                "category": article.get("category", "other"),
                "keywords": article.get("keywords") or [],
                "chunk_index": i,
                "chunk_total": len(segments),
            },
        })
    return chunks
