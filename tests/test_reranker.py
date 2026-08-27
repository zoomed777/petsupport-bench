import copy
from unittest.mock import patch, MagicMock


CANDIDATES = [
    {"id": "doc1", "text": "退款政策说明", "score": 0.5},
    {"id": "doc2", "text": "物流跟踪信息", "score": 0.4},
    {"id": "doc3", "text": "账户安全建议", "score": 0.3},
]


def _fresh_candidates():
    return copy.deepcopy(CANDIDATES)


def test_rerank_returns_ordered_results():
    from app.services.reranker import rerank
    with patch("app.services.reranker._get_reranker") as mock_get:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.7, 0.8]
        mock_get.return_value = mock_model
        results = rerank("退款", _fresh_candidates(), top_k=3)
    assert len(results) == 3
    scores = [r["rerank_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_rerank_respects_top_k():
    from app.services.reranker import rerank
    with patch("app.services.reranker._get_reranker") as mock_get:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.7, 0.8]
        mock_get.return_value = mock_model
        results = rerank("退款", _fresh_candidates(), top_k=2)
    assert len(results) == 2


def test_rerank_empty_candidates():
    from app.services.reranker import rerank
    results = rerank("退款", [], top_k=3)
    assert results == []


def test_rerank_single_candidate():
    from app.services.reranker import rerank
    single = [{"id": "doc1", "text": "退款", "score": 0.5}]
    with patch("app.services.reranker._get_reranker") as mock_get:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.95]
        mock_get.return_value = mock_model
        results = rerank("退款", single, top_k=3)
    assert len(results) == 1
    assert results[0]["rerank_score"] == 0.95


def test_rerank_fallback_when_model_none():
    from app.services.reranker import rerank
    with patch("app.services.reranker._get_reranker", return_value=None):
        results = rerank("退款", _fresh_candidates(), top_k=3)
    assert len(results) == 3
    for r in results:
        assert "rerank_score" in r
        assert r["rerank_score"] == r.get("score", 0.0)


def test_rerank_fallback_sets_rerank_score():
    from app.services.reranker import rerank
    candidates_no_score = [{"id": "doc1", "text": "退款"}]
    with patch("app.services.reranker._get_reranker", return_value=None):
        results = rerank("退款", candidates_no_score, top_k=3)
    assert len(results) == 1
    assert results[0]["rerank_score"] == 0.0
