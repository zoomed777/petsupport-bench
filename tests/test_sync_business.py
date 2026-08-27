from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.agents.ticket_agent import ReActAgent
from app.core.config import get_settings
from app.models.schemas import ChatMessage, ConversationData, ConversationStateEnum


def _make_conv() -> ConversationData:
    return ConversationData(
        conversation_id="conv-1",
        customer_id="C1001",
        order_id="O1001",
        status=ConversationStateEnum.RESOLVED,
        messages=[
            ChatMessage(role="user", content="查订单", created_at="2026-08-06T00:00:00"),
            ChatMessage(role="assistant", content="已为您查询", created_at="2026-08-06T00:00:01"),
        ],
        classification_data={"category": "order", "priority": "normal", "confidence": 0.9},
        thought_chain=["先查订单"],
    )


def _configure_settings(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oms_api_base_url", "http://oms/support")
    monkeypatch.setattr(settings, "support_agent_token", "test-agent-token")


def _fake_response(status_code: int, text: str = "{}") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@patch("app.agents.ticket_agent.httpx.post")
def test_sync_success_logs_and_no_exception(mock_post, monkeypatch, caplog):
    _configure_settings(monkeypatch)
    mock_post.return_value = _fake_response(200, '{"ok": true}')
    caplog.set_level(logging.INFO, logger="app.agents.ticket_agent")

    agent = ReActAgent()
    agent._sync_to_business_api(_make_conv(), "T-123")

    assert mock_post.call_count == 1
    assert any("回写业务后台成功" in r.message for r in caplog.records)


@patch("app.agents.ticket_agent.httpx.post")
def test_sync_403_logs_failure_and_no_raise(mock_post, monkeypatch, caplog):
    _configure_settings(monkeypatch)
    mock_post.return_value = _fake_response(403, "forbidden")
    caplog.set_level(logging.WARNING, logger="app.agents.ticket_agent")

    agent = ReActAgent()
    agent._sync_to_business_api(_make_conv(), "T-123")

    assert mock_post.call_count == 1
    assert any("回写业务后台失败" in r.message for r in caplog.records)


@patch("app.agents.ticket_agent.httpx.post")
def test_sync_post_exception_logs_and_no_raise(mock_post, monkeypatch, caplog):
    _configure_settings(monkeypatch)
    mock_post.side_effect = ConnectionError("boom")
    caplog.set_level(logging.WARNING, logger="app.agents.ticket_agent")

    agent = ReActAgent()
    agent._sync_to_business_api(_make_conv(), "T-123")

    assert mock_post.call_count == 1
    assert any("回写业务后台异常" in r.message for r in caplog.records)


@patch("app.agents.ticket_agent.httpx.post")
def test_sync_missing_token_skips_post(mock_post, monkeypatch, caplog):
    _configure_settings(monkeypatch)
    monkeypatch.setattr(get_settings(), "support_agent_token", None)
    caplog.set_level(logging.WARNING, logger="app.agents.ticket_agent")

    agent = ReActAgent()
    agent._sync_to_business_api(_make_conv(), "T-123")

    assert mock_post.call_count == 0
    assert any("support_agent_token 未配置" in r.message for r in caplog.records)


@patch("app.agents.ticket_agent.httpx.post")
def test_sync_sends_authorization_header(mock_post, monkeypatch):
    _configure_settings(monkeypatch)
    mock_post.return_value = _fake_response(200)

    agent = ReActAgent()
    agent._sync_to_business_api(_make_conv(), "T-123")

    args, kwargs = mock_post.call_args
    assert args[0] == "http://oms/support/api/admin/agent-analyses"
    assert kwargs["headers"]["Authorization"] == "Bearer test-agent-token"
