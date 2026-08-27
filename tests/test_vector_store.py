from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def mock_embedder():
    with patch("app.services.vector_store.SentenceTransformer") as mock_st:
        instance = MagicMock()
        instance.get_sentence_embedding_dimension.return_value = 1024

        def fake_encode(texts, **_):
            if isinstance(texts, str):
                return np.random.randn(1024).astype(np.float32)
            return np.random.randn(len(texts), 1024).astype(np.float32)

        instance.encode.side_effect = fake_encode
        mock_st.return_value = instance
        yield instance


@pytest.fixture
def vs(mock_embedder):
    from app.services.vector_store import VectorStore
    tmpdir = Path(tempfile.mkdtemp())
    store = VectorStore(persist_dir=tmpdir)
    chunks = [
        {"text": "关于退款的政策说明", "metadata": {"doc_id": "REFUND_001", "category": "refund"}},
        {"text": "关于物流的跟踪信息", "metadata": {"doc_id": "LOGISTICS_001", "category": "logistics"}},
        {"text": "关于账户安全的建议", "metadata": {"doc_id": "ACCT_001", "category": "account"}},
    ]
    store.build_index(chunks)
    yield store
    shutil.rmtree(tmpdir)


class TestVectorStoreBuild:
    def test_build_index_returns_chunk_count(self, vs):
        assert vs.count() == 3

    def test_build_index_empty(self, mock_embedder):
        from app.services.vector_store import VectorStore
        store = VectorStore(persist_dir=Path(tempfile.mkdtemp()))
        store.build_index([])
        assert store.count() == 0


class TestVectorStoreSearch:
    def test_search_returns_results(self, vs):
        hits = vs.search("退款", k=3)
        assert len(hits) > 0
        assert all(isinstance(h, dict) for h in hits)

    def test_search_category_filter(self, vs):
        hits = vs.search("退款", category="refund", k=3)
        assert all(h["metadata"]["category"] == "refund" for h in hits)

    def test_search_no_match_returns_empty(self, vs):
        hits = vs.search("zzzzzzzzzzzzzzzzzzzz", k=3)
        assert isinstance(hits, list)

    def test_search_empty_store(self, mock_embedder):
        from app.services.vector_store import VectorStore
        store = VectorStore(persist_dir=Path(tempfile.mkdtemp()))
        hits = store.search("退款", k=3)
        assert hits == []


class TestVectorStorePersistence:
    def test_save_and_load_roundtrip(self, mock_embedder):
        from app.services.vector_store import VectorStore
        tmpdir = Path(tempfile.mkdtemp())
        store1 = VectorStore(persist_dir=tmpdir)
        store1.build_index([
            {"text": "退款政策", "metadata": {"doc_id": "R1", "category": "refund"}},
        ])
        store1._save()

        store2 = VectorStore(persist_dir=tmpdir)
        assert store2.count() == 1
        assert store2.ids == store1.ids
        shutil.rmtree(tmpdir)


class TestVectorStoreCount:
    def test_count_after_build(self, vs):
        assert vs.count() == 3

    def test_count_empty(self, mock_embedder):
        from app.services.vector_store import VectorStore
        store = VectorStore(persist_dir=Path(tempfile.mkdtemp()))
        assert store.count() == 0
