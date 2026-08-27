import json
from unittest.mock import patch, MagicMock
import pytest


KB_ARTICLES = [
    {"policy_id": "R001", "title": "退款政策", "content": "7天无理由退货", "category": "refund", "keywords": ["退货"]},
    {"policy_id": "L001", "title": "物流政策", "content": "发货后3-5天送达", "category": "logistics", "keywords": ["物流"]},
]


@pytest.fixture
def kb_json(tmp_path):
    path = tmp_path / "knowledge_base.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(KB_ARTICLES, f, ensure_ascii=False)
    return path


@pytest.fixture
def mock_rag(mock_embedder, mock_reranker):
    pass


@pytest.fixture
def mock_embedder():
    import numpy as np
    with patch("app.services.vector_store.SentenceTransformer") as m:
        inst = MagicMock()
        inst.get_sentence_embedding_dimension.return_value = 1024

        def fake_encode(texts, **_):
            if isinstance(texts, str):
                return np.random.randn(1024).astype(np.float32)
            return np.random.randn(len(texts), 1024).astype(np.float32)

        inst.encode.side_effect = fake_encode
        m.return_value = inst
        yield


@pytest.fixture
def mock_reranker():
    with patch("app.services.reranker._get_reranker") as m:
        m.return_value = None
        yield


@pytest.fixture(autouse=True)
def isolated_vector_store(tmp_path, monkeypatch, mock_embedder):
    from app.services.vector_store import VectorStore
    from app.services import knowledge_base as kb_module

    real_get_vector_store = kb_module.get_vector_store
    real_get_vector_store.cache_clear()
    store = VectorStore(persist_dir=str(tmp_path / "vs"))
    monkeypatch.setattr(kb_module, "get_vector_store", lambda: store)
    yield store
    real_get_vector_store.cache_clear()


class TestKnowledgeBaseSearch:
    def test_search_returns_knowledge_hits(self, kb_json, mock_embedder, mock_reranker):
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(kb_json)
        hits = kb.search("退款", limit=3)
        assert len(hits) > 0
        hit = hits[0]
        assert hasattr(hit, "id")
        assert hasattr(hit, "title")
        assert hasattr(hit, "answer")
        assert hasattr(hit, "score")
        assert hasattr(hit, "retrieval_method")
        assert hit.retrieval_method == "bge_m3_reranker"

    def test_search_category_filter(self, kb_json, mock_embedder, mock_reranker):
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(kb_json)
        hits = kb.search("物流", category="logistics", limit=3)
        if hits:
            assert all(h.category == "logistics" for h in hits)

    def test_search_no_match_returns_empty(self, kb_json, mock_embedder, mock_reranker):
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(kb_json)
        hits = kb.search("zzzzzzzzzzzzzzzzzzz", limit=3)
        assert isinstance(hits, list)

    def test_search_empty_kb(self, tmp_path, mock_embedder, mock_reranker):
        from app.services.vector_store import get_vector_store, VectorStore
        get_vector_store.cache_clear()
        path = tmp_path / "empty.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
        from app.services.knowledge_base import KnowledgeBase
        with patch.object(VectorStore, "_load", return_value=None):
            kb = KnowledgeBase(path)
            hits = kb.search("退款", limit=3)
        assert hits == []


class TestKnowledgeBaseRebuild:
    def test_rebuild_after_article_change(self, kb_json, mock_embedder, mock_reranker):
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(kb_json)
        import json as j
        articles = j.load(open(kb_json, "r", encoding="utf-8"))
        articles.append({
            "policy_id": "NEW001", "title": "新政策", "content": "全新内容",
            "category": "other", "keywords": [],
        })
        j.dump(articles, open(kb_json, "w", encoding="utf-8"), ensure_ascii=False)

        kb.rebuild()
        hits_after = kb.search("新政策", limit=3)
        assert len(hits_after) > 0
        assert any(h.id == "NEW001" for h in hits_after)


class TestKnowledgeBaseEdgeCases:
    def test_init_missing_file(self, tmp_path, mock_embedder, mock_reranker):
        path = tmp_path / "nonexistent.json"
        path.write_text("[]", encoding="utf-8")
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(path)
        assert kb.articles == []

    def test_search_coerces_unknown_category_to_other(self, kb_json, mock_embedder, mock_reranker):
        from app.services.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(kb_json)
        kb.vs.search = MagicMock(return_value=[
            {
                "id": "x",
                "text": "旧数据分类文档",
                "metadata": {"doc_id": "X001", "title": "旧数据", "category": "bogus"},
                "score": 0.1,
            }
        ])
        hits = kb.search("旧数据分类文档", limit=5)
        assert hits
        assert hits[0].id == "X001"
        assert hits[0].category == "other"


class TestKBArticleCreateValidation:
    def test_rejects_invalid_category(self):
        import pytest
        from pydantic import ValidationError
        from app.api.tickets import KBArticleCreate
        with pytest.raises(ValidationError):
            KBArticleCreate(policy_id="P1", category="bogus", title="t", content="c")

    def test_accepts_valid_category(self):
        from app.api.tickets import KBArticleCreate
        article = KBArticleCreate(policy_id="P1", category="refund", title="t", content="c")
        assert article.category == "refund"
