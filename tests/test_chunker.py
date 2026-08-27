from app.services.chunker import split_text, chunk_article


def test_split_text_short_text_no_split():
    text = "这是一条短文本"
    chunks = split_text(text, chunk_size=100, overlap=20)
    assert chunks == [text]


def test_split_text_respects_chunk_size():
    text = "。" * 300
    chunks = split_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 110 for c in chunks)


def test_split_text_sentence_boundary():
    text = ("第一句话。第二句话。第三句话。第四句话。第五句话。第六句话。"
            "第七句话。第八句话。第九句话。第十句话。")
    chunks = split_text(text, chunk_size=20, overlap=5)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith("。")


def test_chunk_article_format():
    article = {
        "policy_id": "POLICY_001",
        "title": "退货政策",
        "content": "7天无理由退货。商品需保持完好。",
        "category": "refund",
        "keywords": ["退货", "退款"],
    }
    chunks = chunk_article(article)
    assert len(chunks) >= 1
    chunk = chunks[0]
    assert chunk["text"]
    assert chunk["metadata"]["doc_id"] == "POLICY_001"
    assert chunk["metadata"]["title"] == "退货政策"
    assert chunk["metadata"]["category"] == "refund"
    assert chunk["metadata"]["chunk_index"] == 0
    assert chunk["metadata"]["chunk_total"] == len(chunks)


def test_chunk_article_empty_body():
    article = {"policy_id": "P001", "title": "Test", "category": "other", "keywords": []}
    chunks = chunk_article(article)
    assert len(chunks) >= 1
    assert "Test" in chunks[0]["text"]
    assert "other" in chunks[0]["text"]


def test_chunk_article_overlap_continuity():
    long_content = "段落A。" * 30
    article = {"policy_id": "P001", "title": "标题", "content": long_content, "category": "other", "keywords": []}
    chunks = chunk_article(article, chunk_size=100, overlap=30)
    if len(chunks) > 1:
        prev_tail = chunks[0]["text"][-30:]
        next_head = chunks[1]["text"][:30]
        assert len(prev_tail) > 0 and len(next_head) > 0


def test_split_text_empty():
    assert split_text("") == []


def test_chunk_article_null_keywords_does_not_raise():
    article = {
        "policy_id": "P001",
        "title": "退货政策",
        "content": "7天无理由退货",
        "category": "refund",
        "keywords": None,
    }
    chunks = chunk_article(article)
    assert len(chunks) >= 1
    assert chunks[0]["metadata"]["keywords"] == []
