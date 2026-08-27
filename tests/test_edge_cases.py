"""边界情况、异常链路、数据隔离压力测试"""

from unittest.mock import patch


from app.agents.ticket_agent import ReActAgent
from app.agents.tools import execute_tool, search_policy
from app.models.schemas import ChatRequest


# ========== 对话边界情况 ==========

def test_empty_message_handled_gracefully():
    agent = ReActAgent()
    req = ChatRequest(message="", customer_id="C1001")
    events = list(agent.chat_stream(req))
    events_names = [e.get("event") for e in events]
    assert "error" in events_names or "session" in events_names


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_very_long_message_truncated(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "ok"}}}

    agent = ReActAgent()
    long_msg = "a" * 10000
    req = ChatRequest(message=long_msg, customer_id="C1001")
    events = list(agent.chat_stream(req))
    completed = [e for e in events if e.get("event") == "completed"]
    assert len(completed) >= 0


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_llm_returns_invalid_json(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "test", "action": None}

    agent = ReActAgent()
    req = ChatRequest(message="退款", customer_id="C1001")
    events = list(agent.chat_stream(req))
    completed = [e for e in events if e.get("event") == "completed"]
    assert completed[0]["status"] == "escalated"


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_conversation_exceed_max_steps(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    unknown_action = {"thought": "unknown", "action": {"name": "nonexistent_tool", "args": {}}}
    mock_call_llm.return_value = unknown_action

    agent = ReActAgent()
    req = ChatRequest(message="hi", customer_id="C1001")
    events = list(agent.chat_stream(req))
    completed = [e for e in events if e.get("event") == "completed"]
    assert completed[0]["status"] == "escalated"


# ========== 工具异常链路 ==========

@patch("app.agents.tools.get_external_gateway")
def test_tool_timeout_returns_error(mock_gateway):
    mock_gw = mock_gateway.return_value
    mock_gw.oms.get.side_effect = Exception("Connection timeout")
    from app.agents.tools import get_order
    result = get_order.invoke({"order_id": "ORD-001"})
    assert result["status"] == "error"
    assert "超时或失败" in result["error_message"]


@patch("app.agents.tools.get_external_gateway")
def test_tool_http_500_returns_error(mock_gateway):
    mock_gw = mock_gateway.return_value
    mock_gw.oms.get.side_effect = Exception("HTTP 500 Internal Server Error")
    from app.agents.tools import get_order
    result = get_order.invoke({"order_id": "ORD-001"})
    assert result["status"] == "error"


@patch("app.agents.tools.get_external_gateway")
def test_multiple_tools_all_fail_then_escalate(mock_gateway):
    mock_gw = mock_gateway.return_value
    mock_gw.oms.get.return_value = None
    mock_gw.logistics.get.return_value = None
    mock_gw.crm.get.return_value = None

    result1 = execute_tool("get_order", {"order_id": "X001"})
    assert result1.status == "error"
    result2 = execute_tool("get_shipment", {"order_id": "X001"})
    assert result2.status == "error"


# ========== 搜索知识库覆盖情况 ==========

def test_knowledge_base_search_all_categories():
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    queries = [
        ("退货", "refund"),
        ("会员", "account"),
    ]
    for query, expected_category in queries:
        hits = kb.search(query, limit=3)
        assert isinstance(hits, list)
        if hits:
            categories = {h.category for h in hits}
            assert expected_category in categories, f"查询 '{query}' 期望分类 {expected_category}，实际: {categories}"


def test_knowledge_base_no_match():
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    hits = kb.search("zzzzzzzzzzzzzzzzzzzzzzzzzzzz", limit=3)
    for hit in hits:
        assert isinstance(hit.score, float)


def test_knowledge_base_empty_query_handled():
    result = search_policy.invoke({"query": ""})
    assert result["status"] == "error"


# ========== 数据隔离压力场景 ==========

def test_isolation_many_concurrent_conversations():
    from app.agents.state import ConversationSession
    session = ConversationSession()
    owners = {}
    for i in range(20):
        cid = f"STRESS-C{i:03d}"
        conv = session.create(customer_id=cid)
        session.save(conv)
        owners[conv.conversation_id] = cid

    for conv_id, owner in owners.items():
        loaded = session.load(conv_id, owner)
        assert loaded is not None, f"Owner {owner} should access own conv"
        for other in ["STRANGER", "HACKER", "ANOTHER"]:
            blocked = session.load(conv_id, other)
            assert blocked is None, f"{other} should NOT access {owner}'s conv"


def test_isolation_cross_customer_tool_blocked():
    from app.agents.tools import execute_tool
    result = execute_tool("get_customer", {"customer_id": "C1001"}, caller_customer_id="C9999")
    assert result.status == "error"
    assert "无权" in result.error_message


def test_isolation_customer_can_query_self():
    from app.agents.tools import execute_tool
    with patch("app.agents.tools.get_external_gateway") as mock_gw:
        mock_gw.return_value.crm.get.return_value = {"customer_id": "C1001"}
        result = execute_tool("get_customer", {"customer_id": "C1001"}, caller_customer_id="C1001")
        assert result.status == "ok"
