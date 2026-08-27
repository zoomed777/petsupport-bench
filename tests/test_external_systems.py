import time

import pytest

from app.core.config import Settings
from app.services.external_systems import ExternalSystemGateway, IntegrationApiClient


@pytest.mark.asyncio
async def test_mutating_timeout_never_reruns():
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        time.sleep(0.1)
        return "ok"

    client = IntegrationApiClient("http://x", None, 0.01)
    result = client._run_async(slow, mutating=True)

    assert result is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_non_mutating_timeout_reruns_synchronously():
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        time.sleep(0.1)
        return "ok"

    client = IntegrationApiClient("http://x", None, 0.01)
    result = client._run_async(slow, mutating=False)

    assert result == "ok"
    assert calls["n"] == 2


def test_get_acting_for_overrides_default_header():
    client = IntegrationApiClient("http://x", None, 0.01, acting_for="default-agent")
    captured = {}

    def fake_do_get(url, headers):
        captured.update(headers)
        return {"data": {"x": 1}}

    client._do_get = fake_do_get
    client.get("/orders/1", acting_for="99")

    assert captured.get("X-Agent-For") == "99"


def test_get_omits_acting_for_header_when_unset():
    client = IntegrationApiClient("http://x", None, 0.01)
    captured = {}

    def fake_do_get(url, headers):
        captured.update(headers)
        return {"data": {"x": 1}}

    client._do_get = fake_do_get
    client.get("/orders/1")

    assert "X-Agent-For" not in captured
    assert captured.get("Authorization") is None


def test_gateway_prefers_support_agent_token():
    settings = Settings(
        support_agent_token="agent-token",
        crm_api_base_url="http://crm",
        oms_api_base_url="http://oms",
        logistics_api_base_url="http://logistics",
    )
    gw = ExternalSystemGateway(settings)

    assert gw.crm.token == "agent-token"
    assert gw.oms.token == "agent-token"
    assert gw.logistics.token == "agent-token"


def test_gateway_falls_back_to_per_client_tokens():
    settings = Settings(
        crm_api_base_url="http://crm",
        crm_api_token="crm-token",
        oms_api_base_url="http://oms",
        oms_api_token="oms-token",
        logistics_api_base_url="http://logistics",
        logistics_api_token="logistics-token",
    )
    gw = ExternalSystemGateway(settings)

    assert gw.crm.token == "crm-token"
    assert gw.oms.token == "oms-token"
    assert gw.logistics.token == "logistics-token"


def test_create_appointment_rejected_business_error(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"code": 40012, "data": None})

    assert gw.create_appointment({"serviceType": "BATH"}) is None


def test_create_appointment_success_returns_payload(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"code": 200, "data": {"id": 1}})

    result = gw.create_appointment({"serviceType": "BATH"})
    assert result == {"code": 200, "data": {"id": 1}}


def test_create_appointment_unwrapped_success_passes(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"id": 1, "serviceType": "BATH", "status": "PENDING"})

    result = gw.create_appointment({"serviceType": "BATH"})
    assert result == {"id": 1, "serviceType": "BATH", "status": "PENDING"}


def test_mark_order_processed_rejected_business_error(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"code": 40011, "data": None})

    result = gw.mark_order_processed("ORD-1", "已处理")
    assert result.attempted is True
    assert result.success is False
    assert result.order_id == "ORD-1"


def test_mark_order_processed_success(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"code": 200, "data": {"orderId": "ORD-1", "status": "PROCESSED"}})

    result = gw.mark_order_processed("ORD-1", "已处理")
    assert result.attempted is True
    assert result.success is True
    assert result.order_id == "ORD-1"


def test_mark_order_processed_unwrapped_success_passes(monkeypatch):
    settings = Settings(oms_api_base_url="http://oms")
    gw = ExternalSystemGateway(settings)
    monkeypatch.setattr(gw.oms, "post", lambda *a, **k: {"orderId": "ORD-1", "status": "PROCESSED"})

    result = gw.mark_order_processed("ORD-1", "已处理")
    assert result.attempted is True
    assert result.success is True
    assert result.order_id == "ORD-1"
