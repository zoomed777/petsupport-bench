from __future__ import annotations

from unittest.mock import patch, MagicMock


from app.agents.ticket_agent import ReActAgent
from app.agents.state import session_manager
from app.models.schemas import ChatMessage, ChatRequest


def _mock_llm_response(thought: str, action_name: str, action_args: dict | None = None) -> MagicMock:
    """Create a mock OpenAI response that returns the given thought/action."""
    import json
    mock_choice = MagicMock()
    action_part = json.dumps({"name": action_name, "args": action_args or {}}, ensure_ascii=False)
    mock_choice.message.content = json.dumps({"thought": thought, "action": json.loads(action_part)}, ensure_ascii=False)
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 50
    mock_usage.completion_tokens = 30
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    return mock_response


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_chat_stream_yields_session_and_thought(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "我需要查询订单", "action": {"name": "reply_user", "args": {"message": "您好"}}}

    agent = ReActAgent()
    req = ChatRequest(message="查询我的订单", customer_id="C1001")
    events = list(agent.chat_stream(req))

    events_names = [e.get("event") for e in events]
    assert "session" in events_names
    assert "thought" in events_names
    assert "reply" in events_names or "ask_user" in events_names


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_chat_ends_with_reply_when_reply_user(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "直接回复", "action": {"name": "reply_user", "args": {"message": "已为您查询到信息"}}}

    agent = ReActAgent()
    req = ChatRequest(message="查订单", customer_id="C1001")
    events = list(agent.chat_stream(req))
    completed = [e for e in events if e.get("event") == "completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "resolved"


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_chat_escalates_on_llm_error(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = None

    agent = ReActAgent()
    req = ChatRequest(message="退款", customer_id="C1001")
    events = list(agent.chat_stream(req))
    completed = [e for e in events if e.get("event") == "completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "escalated"


@patch("app.agents.ticket_agent.get_settings")
def test_chat_returns_error_when_llm_disabled(mock_settings):
    mock_settings.return_value.llm_enabled = False
    mock_settings.return_value.llm_api_key = None

    agent = ReActAgent()
    req = ChatRequest(message="查订单", customer_id="C1001")
    events = list(agent.chat_stream(req))
    errors = [e for e in events if e.get("event") == "error"]
    assert len(errors) >= 1


def test_get_or_create_conversation_creates_new():
    agent = ReActAgent()
    req = ChatRequest(message="查订单", customer_id="C1001")
    conv = agent._get_or_create_conversation(req)
    assert conv.customer_id == "C1001"
    assert conv.conversation_id


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_chat_stream_injects_live_data_context(mock_settings, mock_call_llm):
    import json
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "hi"}}}

    agent = ReActAgent()
    req = ChatRequest(message="你好", customer_id="C1001", live_data={"point_info": {"balance": 100}})
    events = list(agent.chat_stream(req))
    conv_id = next(e["conversation_id"] for e in events if e.get("event") == "session")

    conv = session_manager.peek(conv_id)
    ctx = [m for m in conv.messages if m.tool_name == "system_context"]
    assert len(ctx) == 1
    assert json.loads(ctx[0].content)["live_data"] == {"point_info": {"balance": 100}}


def test_chat_stream_live_data_not_duplicated_on_resume():
    import json
    conv = session_manager.create(customer_id="C1001")
    conv.messages.append(ChatMessage(role="tool", content=json.dumps({"live_data": {"point_info": {"balance": 100}}}, ensure_ascii=False), tool_name="system_context", created_at="now"))
    session_manager.save(conv)

    agent = ReActAgent()
    req = ChatRequest(message="继续", customer_id="C1001", conversation_id=conv.conversation_id, live_data={"point_info": {"balance": 100}})
    with patch.object(agent, "_call_llm") as mock_llm, patch("app.agents.ticket_agent.get_settings") as mock_settings:
        mock_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "ok"}}}
        mock_settings.return_value.llm_enabled = True
        mock_settings.return_value.llm_api_key = "test-key"
        mock_settings.return_value.llm_model = "test-model"
        list(agent.chat_stream(req))

    reloaded = session_manager.peek(conv.conversation_id)
    ctx = [m for m in reloaded.messages if m.tool_name == "system_context"]
    assert len(ctx) == 1


def test_get_or_create_conversation_uses_existing():
    agent = ReActAgent()
    req1 = ChatRequest(message="查订单", customer_id="C1001")
    conv1 = agent._get_or_create_conversation(req1)
    req2 = ChatRequest(message="继续", customer_id="C1001", conversation_id=conv1.conversation_id)
    conv2 = agent._get_or_create_conversation(req2)
    assert conv2.conversation_id == conv1.conversation_id


def test_parse_llm_response_handles_json():
    agent = ReActAgent()
    result = agent._parse_json_response('{"thought": "test", "action": {"name": "reply_user", "args": {}}}')
    assert result is not None
    assert result["thought"] == "test"


def test_parse_llm_response_handles_markdown_fence():
    agent = ReActAgent()
    text = '```\n{"thought": "test", "action": {"name": "reply_user", "args": {}}}\n```'
    result = agent._parse_json_response(text)
    assert result is not None
    assert result["thought"] == "test"


def test_parse_llm_response_no_json():
    agent = ReActAgent()
    assert agent._parse_json_response("not json") is None


def test_extract_escalation_reason_reads_appended_tool_message():
    agent = ReActAgent()
    conv = session_manager.create(customer_id="C1001")
    conv.messages.append(ChatMessage(
        role="tool",
        content='{"data": {"reason": "客户要求退款，需人工处理", "summary": ""}}',
        tool_name="escalate_human",
        created_at="now",
    ))
    assert agent._extract_escalation_reason(conv) == "客户要求退款，需人工处理"


def test_extract_escalation_reason_falls_back_to_default():
    agent = ReActAgent()
    conv = session_manager.create(customer_id="C1001")
    conv.messages.append(ChatMessage(role="user", content="我要退款", created_at="now"))
    assert agent._extract_escalation_reason(conv) == "转人工"


@patch("app.agents.ticket_agent.ReActAgent._persist_conversation")
@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_escalate_human_records_real_reason(mock_settings, mock_call_llm, mock_persist):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    reason = "退款金额超过 1000 元，需人工审批"
    mock_call_llm.return_value = {
        "thought": "退款需人工",
        "action": {"name": "escalate_human", "args": {"reason": reason, "summary": "退款流程"}},
    }

    agent = ReActAgent()
    conv = session_manager.create(customer_id="C1001")
    conv.messages.append(ChatMessage(role="user", content="我要退款", created_at=agent._now()))
    events = list(agent._react_loop(conv))

    completed = [e for e in events if e.get("event") == "completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "escalated"
    assert agent._extract_escalation_reason(conv) == reason
    mock_persist.assert_called_once()


@patch("app.agents.ticket_agent.get_settings")
def test_run_sync_escalate_human_returns_reason(mock_settings):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    reason = "投诉需人工介入"

    agent = ReActAgent()
    with patch.object(agent, "_call_llm") as mock_call_llm:
        mock_call_llm.return_value = {
            "thought": "转人工",
            "action": {"name": "escalate_human", "args": {"reason": reason}},
        }
        result = agent.run_sync("我要投诉", customer_id="C1001")

    assert result["should_escalate"] is True
    assert result["escalation_reason"] == reason
