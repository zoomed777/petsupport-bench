from unittest.mock import patch


from app.agents.tools import (
    execute_tool,
    TOOLS,
    get_order,
    ask_user,
    escalate_human,
    reply_user,
)


def test_tools_contain_all_expected():
    names = {t.name for t in TOOLS}
    assert names == {
        "get_order", "get_shipment", "get_customer",
        "search_policy", "search_pet_care", "check_slots", "create_booking",
        "ask_user", "escalate_human", "reply_user",
        "get_flash_activities", "search_products", "get_group_activities", "get_point_info",
    }


def test_ask_user_returns_question():
    result = ask_user.invoke({"question": "What is your order ID?"})
    assert result["status"] == "ok"
    assert result["data"]["question"] == "What is your order ID?"


def test_ask_user_empty_error():
    result = ask_user.invoke({"question": ""})
    assert result["status"] == "error"
    assert result["error_message"] is not None


def test_escalate_human_returns_escalated():
    result = escalate_human.invoke({"reason": "退款需求", "summary": "用户要求退款"})
    assert result["status"] == "ok"
    assert result["data"]["escalated"] is True
    assert result["data"]["reason"] == "退款需求"
    assert result["data"]["summary"] == "用户要求退款"


def test_escalate_human_empty_error():
    result = escalate_human.invoke({"reason": "", "summary": ""})
    assert result["status"] == "error"


def test_reply_user_returns_message():
    result = reply_user.invoke({"message": "您好，已为您查询到订单信息。"})
    assert result["status"] == "ok"
    assert result["data"]["message"] == "您好，已为您查询到订单信息。"


def test_reply_user_empty_error():
    result = reply_user.invoke({"message": ""})
    assert result["status"] == "error"


@patch("app.agents.tools.get_external_gateway")
def test_get_order_found(mock_get_gateway):
    mock_gw = mock_get_gateway.return_value
    mock_gw.oms.get.return_value = {"order_id": "ORD-001", "status": "shipped"}
    result = get_order.invoke({"order_id": "ORD-001"})
    assert result["status"] == "ok"
    assert result["data"]["order_id"] == "ORD-001"


@patch("app.agents.tools.get_external_gateway")
def test_get_order_not_found(mock_get_gateway):
    mock_gw = mock_get_gateway.return_value
    mock_gw.oms.get.return_value = None
    result = get_order.invoke({"order_id": "ORD-999"})
    assert result["status"] == "error"


def test_get_order_missing_id():
    result = execute_tool("get_order", {})
    assert result.status == "error"


@patch("app.agents.tools.get_external_gateway")
def test_get_customer_self_isolation_ok(mock_get_gateway):
    mock_gw = mock_get_gateway.return_value
    mock_gw.crm.get.return_value = {"customer_id": "C1001"}
    result = execute_tool("get_customer", {"customer_id": "C1001"}, caller_customer_id="C1001")
    assert result.status == "ok"


def test_get_customer_isolation_blocked():
    result = execute_tool("get_customer", {"customer_id": "C1002"}, caller_customer_id="C1001")
    assert result.status == "error"
    assert "无权" in result.error_message


def test_get_point_info_isolation_blocked():
    result = execute_tool("get_point_info", {"customer_id": "C1002"}, caller_customer_id="C1001")
    assert result.status == "error"
    assert "无权" in result.error_message


def test_create_booking_isolation_blocked():
    result = execute_tool("create_booking", {"user_id": "U999", "service_type": "BATH", "appoint_date": "2026-08-10", "appoint_time": "09:00-10:00"}, caller_customer_id="U001")
    assert result.status == "error"
    assert "无权" in result.error_message


@patch("app.agents.tools.get_external_gateway")
def test_get_order_passes_acting_for(mock_get_gateway):
    mock_gw = mock_get_gateway.return_value
    mock_gw.oms.get.return_value = {"order_id": "ORD-001", "status": "shipped"}
    result = execute_tool("get_order", {"order_id": "ORD-001"}, caller_customer_id="C0042")
    assert result.status == "ok"
    mock_gw.oms.get.assert_called_with("/orders/ORD-001", acting_for="C0042")


def test_acting_for_not_leaked_after_execute():
    from app.agents.tools import _acting_for_ctx
    result = execute_tool("get_order", {}, caller_customer_id="C0042")
    assert result.status == "error"
    assert _acting_for_ctx.get() is None


def test_execute_tool_unknown():
    result = execute_tool("nonexistent", {})
    assert result.status == "error"


def test_execute_tool_error_args():
    result = execute_tool("get_order", {})
    assert result.status == "error"
