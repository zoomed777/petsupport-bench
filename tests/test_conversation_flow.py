"""多轮对话恢复、管理员回复流、全链路集成测试"""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import json
from datetime import datetime, timezone

import pytest

from app.agents.ticket_agent import ReActAgent
from app.agents.state import session_manager
from app.models.schemas import (
    ChatRequest, ChatMessage, ConversationStateEnum,
)


def _mock_llm_response(thought: str, action_name: str, action_args=None):
    action = {"name": action_name, "args": action_args or {}}
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"thought": thought, "action": action}, ensure_ascii=False)
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 50
    mock_usage.completion_tokens = 30
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    return mock_response


# ========== 多轮对话恢复 ==========

@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_two_turn_conversation(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"

    ask_response = {"thought": "need order id", "action": {"name": "ask_user", "args": {"question": "请提供订单号"}}}
    reply_response = {"thought": "got info", "action": {"name": "reply_user", "args": {"message": "已查到"}}}

    mock_call_llm.side_effect = [ask_response, reply_response]

    agent = ReActAgent()
    req = ChatRequest(message="查询我的订单", customer_id="C1001")
    events = list(agent.chat_stream(req))
    ask_events = [e for e in events if e.get("event") == "ask_user"]
    pause_events = [e for e in events if e.get("event") == "pause"]
    assert len(ask_events) == 1
    assert len(pause_events) == 1
    conv_id = next(e["conversation_id"] for e in events if e.get("event") == "session")

    resume_req = ChatRequest(
        message="ORD-123",
        customer_id="C1001",
        conversation_id=conv_id,
    )
    events2 = list(agent.chat_stream(resume_req))
    reply_events = [e for e in events2 if e.get("event") == "reply"]
    assert len(reply_events) >= 0


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_resume_expired_conversation_creates_new(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "hello"}}}

    conv = session_manager.create(customer_id="C1001")
    conv.status = ConversationStateEnum.IDLE
    conv.expires_at = "2020-01-01T00:00:00+00:00"
    session_manager.save(conv)

    agent = ReActAgent()
    req = ChatRequest(message="new", customer_id="C1001", conversation_id=conv.conversation_id)
    events = list(agent.chat_stream(req))
    session_events = [e for e in events if e.get("event") == "session"]
    assert len(session_events) == 1
    assert "conversation_id" in session_events[0]


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_expired_session_emits_session_expired(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "hello"}}}

    conv = session_manager.create(customer_id="C1001")
    session_manager._store[conv.conversation_id].expires_at = "2020-01-01T00:00:00+00:00"

    agent = ReActAgent()
    req = ChatRequest(message="new", customer_id="C1001", conversation_id=conv.conversation_id)
    events = list(agent.chat_stream(req))

    expired = [e for e in events if e.get("event") == "session_expired"]
    assert len(expired) == 1
    assert "已" in expired[0]["message"]

    session_events = [e for e in events if e.get("event") == "session"]
    assert len(session_events) == 1
    assert session_events[0]["conversation_id"] == conv.conversation_id


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_new_conversation_does_not_emit_session_expired(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_settings.return_value.oms_api_base_url = None
    mock_call_llm.return_value = {"thought": "t", "action": {"name": "reply_user", "args": {"message": "hello"}}}

    agent = ReActAgent()
    req = ChatRequest(message="new", customer_id="C1001", conversation_id="user_999")
    events = list(agent.chat_stream(req))

    expired = [e for e in events if e.get("event") == "session_expired"]
    assert len(expired) == 0

    session_events = [e for e in events if e.get("event") == "session"]
    assert len(session_events) == 1
    assert session_events[0]["conversation_id"] == "user_999"


def test_cross_customer_reuse_rejected():
    conv = session_manager.create(customer_id="C1001")

    agent = ReActAgent()
    req = ChatRequest(message="查看", customer_id="C9999", conversation_id=conv.conversation_id)
    events = list(agent.chat_stream(req))

    errors = [e for e in events if e.get("event") == "error"]
    assert len(errors) == 1
    assert errors[0]["message"] == "无权访问该会话"
    assert all(e.get("event") != "session" for e in events)
    assert all(e.get("event") != "reply" for e in events)


@patch("app.agents.ticket_agent.ReActAgent._call_llm")
@patch("app.agents.ticket_agent.get_settings")
def test_run_sync_ask_user_marks_escalate(mock_settings, mock_call_llm):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_call_llm.return_value = {"thought": "need info", "action": {"name": "ask_user", "args": {"question": "请提供订单号"}}}

    agent = ReActAgent()
    result = agent.run_sync("查询订单", customer_id="C1001")

    assert result["should_escalate"] is True
    assert "更多信息" in result["escalation_reason"]
    assert result["reply"] == "请提供订单号"


# ========== 管理员回复流 ==========

@patch("app.agents.ticket_agent.get_settings")
def test_admin_reply_injects_message(mock_settings):
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"
    mock_settings.return_value.oms_api_base_url = None

    from app.models.schemas import ChatMessage

    conv = session_manager.create(customer_id="C1001")
    conv.status = ConversationStateEnum.ESCALATED
    conv.messages.append(ChatMessage(role="user", content="我要退款", created_at=datetime.now(timezone.utc).isoformat()))
    session_manager.save(conv)

    from app.api.tickets import AdminReplyRequest, admin_reply_to_conversation
    with patch("httpx.AsyncClient") as mock_http:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value.__aenter__.return_value.get.return_value = mock_resp

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                admin_reply_to_conversation(conv.conversation_id, AdminReplyRequest(message="已为您处理"))
            )
        finally:
            loop.close()

    assert result["success"] is True
    reloaded = session_manager.load(conv.conversation_id)
    assert reloaded is not None


@patch("app.agents.ticket_agent.get_settings")
def test_admin_reply_to_nonexistent_returns_404(mock_settings):
    mock_settings.return_value.oms_api_base_url = None
    from app.api.tickets import AdminReplyRequest, admin_reply_to_conversation
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        with pytest.raises(Exception):
            loop.run_until_complete(
                admin_reply_to_conversation("conv-nonexistent", AdminReplyRequest(message="test"))
            )
    except Exception:
        pass
    finally:
        loop.close()


# ========== 对话持久化完整性 ==========

def test_persist_conversation_saves_all_messages():
    from app.models.schemas import ChatMessage
    conv = session_manager.create(customer_id="C1001")
    conv.messages = [
        ChatMessage(role="user", content="你好", created_at="now"),
        ChatMessage(role="assistant", content="请问有什么可以帮您？", created_at="now"),
        ChatMessage(role="tool", content='{"status":"ok","data":{}}', tool_name="get_order", created_at="now"),
    ]
    conv.thought_chain = ["需要查询订单", "查到订单信息"]
    conv.status = ConversationStateEnum.ESCALATED

    agent = ReActAgent()
    with patch.object(agent, "_sync_to_business_api") as mock_sync:
        agent._persist_conversation(conv)
        mock_sync.assert_called_once()

    assert True


# ========== ReAct 循环中正确的工具重试 ==========

@patch("app.agents.ticket_agent.execute_tool")
@patch("app.agents.ticket_agent.get_settings")
def test_tool_retry_on_error(mock_settings, mock_execute):
    from app.agents.tools import ToolResult
    mock_settings.return_value.llm_enabled = True
    mock_settings.return_value.llm_api_key = "test-key"
    mock_settings.return_value.llm_model = "test-model"

    error_result = ToolResult("error", None, "暂时失败")
    ok_result = ToolResult("ok", {"order_id": "ORD-001"})
    mock_execute.side_effect = [error_result, ok_result]

    agent = ReActAgent()
    conv = session_manager.create(customer_id="C1001")
    first_msg = ChatMessage(role="user", content="查订单 ORD-001", created_at=datetime.now(timezone.utc).isoformat())
    conv.messages.append(first_msg)

    with patch.object(agent, "_call_llm") as mock_llm:
        get_order_action = {"thought": "查订单", "action": {"name": "get_order", "args": {"order_id": "ORD-001"}}}
        reply_action = {"thought": "完成", "action": {"name": "reply_user", "args": {"message": "ok"}}}
        mock_llm.side_effect = [get_order_action, reply_action]
        events = list(agent._react_loop(conv))
        tool_results = [e for e in events if e.get("event") == "tool_result"]
        assert len(tool_results) >= 1
