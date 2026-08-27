from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.models.schemas import BusinessSyncResult, CustomerContext, ExternalContext, OrderContext, ProductContext, ShipmentContext

logger = logging.getLogger(__name__)


class IntegrationApiClient:
    def __init__(self, base_url: str | None, token: str | None, timeout_seconds: float, acting_for: str | None = None):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.acting_for = acting_for
        self._sync_client: httpx.Client | None = None

    @property
    def ready(self) -> bool:
        return bool(self.base_url)

    def _get_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=self.timeout_seconds, trust_env=False)
        return self._sync_client

    def _run_async(self, callable, *args, mutating=False, **kwargs):
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, callable, *args, **kwargs)
            return future.result(self.timeout_seconds)
        except (RuntimeError, TypeError, concurrent.futures.TimeoutError, concurrent.futures.InvalidStateError):
            # 失败（无事件循环 / 调用方异常 / 超时 / Future 状态异常）时：
            # 变更类请求（POST, mutating=True）绝不重跑，避免重复提交；
            # 只读请求（GET）可同步重跑一次。
            if mutating:
                return None
            return callable(*args, **kwargs)

    def _do_get(self, url: str, headers: dict) -> dict[str, Any] | None:
        client = self._get_client()
        response = client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _do_post(self, url: str, headers: dict, payload: dict) -> dict[str, Any] | None:
        client = self._get_client()
        response = client.post(url, headers=headers, json=payload)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get(self, path_template: str, acting_for: str | None = None, **params: str | None) -> dict[str, Any] | None:
        if not self.ready:
            return None
        try:
            path = self._format_path(path_template, params)
        except KeyError:
            return None
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"
        if acting_for or self.acting_for:
            headers["X-Agent-For"] = acting_for or self.acting_for

        try:
            data = self._run_async(self._do_get, url, headers)
        except Exception as exc:
            logger.warning("External API request failed: %s", exc)
            return None

        return unwrap_payload(data)

    def post(self, path_template: str, payload: dict[str, Any], acting_for: str | None = None, **params: str | None) -> dict[str, Any] | None:
        if not self.ready:
            return None
        try:
            path = self._format_path(path_template, params)
        except KeyError:
            return None
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"
        if acting_for or self.acting_for:
            headers["X-Agent-For"] = acting_for or self.acting_for

        try:
            data = self._run_async(self._do_post, url, headers, payload, mutating=True)
        except Exception as exc:
            logger.warning("External API POST failed: %s", exc)
            return None

        return unwrap_payload(data) or data

    def _format_path(self, path_template: str, params: dict[str, str | None]) -> str:
        safe_params = {
            key: quote(str(value), safe="")
            for key, value in params.items()
            if value is not None
        }
        path = path_template.format(**safe_params)
        return path if path.startswith("/") else f"/{path}"


class ExternalSystemGateway:
    """HTTP adapter boundary for CRM, OMS, and logistics systems."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # 统一使用 support_agent_token 作为 agent 身份凭据（对应 Java 端 support.agent.token）；
        # 未配置时回退到各客户端自身的 *_API_TOKEN（向后兼容）。
        token = self.settings.support_agent_token or None
        self.crm = IntegrationApiClient(
            self.settings.crm_api_base_url,
            token or self.settings.crm_api_token,
            self.settings.external_api_timeout_seconds,
            acting_for=None,
        )
        self.oms = IntegrationApiClient(
            self.settings.oms_api_base_url,
            token or self.settings.oms_api_token,
            self.settings.external_api_timeout_seconds,
            acting_for=None,
        )
        self.logistics = IntegrationApiClient(
            self.settings.logistics_api_base_url,
            token or self.settings.logistics_api_token,
            self.settings.external_api_timeout_seconds,
            acting_for=None,
        )

    def _oms_get(self, path: str, query_params: dict[str, str] | None = None, acting_for: str | None = None) -> list[dict] | None:
        """Direct GET to OMS with optional query params, returns unwrapped list."""
        if not self.oms.ready:
            return None
        url = f"{self.oms.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.oms.token:
            headers["Authorization"] = self.oms.token if self.oms.token.lower().startswith("bearer ") else f"Bearer {self.oms.token}"
        if acting_for:
            headers["X-Agent-For"] = acting_for

        def _do():
            client = self.oms._get_client()
            response = client.get(url, headers=headers, params=query_params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data.get("data") or data.get("result") or []
            return data if isinstance(data, list) else []

        try:
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, _do).result()
        except RuntimeError:
            return _do()
        except concurrent.futures.InvalidStateError:
            return _do()
        except Exception as exc:
            logger.warning("OMS direct GET failed: %s -> %s", url, exc)
            return None

    def get_flash_activities(self) -> list[dict]:
        return self._oms_get("/flash-activities") or []
    def get_group_activities(self) -> list[dict]:
        return self._oms_get("/group-activities") or []
    def search_products(self, keyword: str) -> list[dict]:
        if not keyword:
            return []
        return self._oms_get("/search-products", {"q": keyword}) or []

    def get_appointment_slots(self, date: str, service_type: str, acting_for: str | None = None) -> list[str]:
        try:
            from urllib.parse import urlencode
            params = urlencode({"date": date, "serviceType": service_type})
            result = self.oms.get(f"/appointments/slots?{params}", acting_for=acting_for)
            return result.get("data") if isinstance(result, dict) else (result or [])
        except Exception as exc:
            logger.warning("get_appointment_slots failed: %s", exc)
            return []

    def create_appointment(self, payload: dict, acting_for: str | None = None) -> dict | None:
        try:
            result = self.oms.post("/appointments", payload, acting_for=acting_for)
            if isinstance(result, dict):
                code = result.get("code")
                data = result.get("data")
                # Java 业务错误返回 HTTP200 + {code:400xx, data:null}，需判 code==200 且 data 非空才算成功；
                # 成功时 post 已把 data 解包为业务对象，不含 code/data 信封键，不命中以下判断。
                if (code is not None and code != 200) or ("data" in result and data is None):
                    logger.warning("create_appointment rejected by backend: %s", result)
                    return None
            return result
        except Exception as exc:
            logger.warning("create_appointment failed: %s", exc)
            return None

    def get_point_info(self, customer_id: str | None, order_id: str | None = None, acting_for: str | None = None) -> dict | None:
        if not customer_id or customer_id.strip().lower() in ("null", "none", ""):
            logger.warning("get_point_info skipped: invalid customer_id=%r", customer_id)
            return None
        from urllib.parse import urlencode
        params = {'userId': customer_id}
        if order_id:
            params['orderNo'] = order_id
        result = self.oms.get(f"/point-info?{urlencode(params)}", acting_for=acting_for)
        logger.info("get_point_info result for customer_id=%s: %s", customer_id, result)
        return result

    def get_context(self, customer_id: str | None, order_id: str | None) -> ExternalContext:
        customer_data = self.crm.get(
            self.settings.crm_customer_path_template,
            customer_id=customer_id,
        )
        order_data = self.oms.get(
            self.settings.oms_order_path_template,
            order_id=order_id,
        )
        shipment_data = self.logistics.get(
            self.settings.logistics_shipment_path_template,
            order_id=order_id,
        )
        product_data = None
        if order_data:
            items = normalize_items(order_data.get("items", []))
            if items:
                product_data = self.oms.get("/products/by-name/{product_name}", product_name=items[0])

        return ExternalContext(
            customer=normalize_customer(customer_data) if customer_data else None,
            order=normalize_order(order_data) if order_data else None,
            shipment=normalize_shipment(shipment_data) if shipment_data else None,
            product=normalize_product(product_data) if product_data else None,
        )

    def mark_order_processed(self, order_id: str | None, note: str) -> BusinessSyncResult:
        if not order_id:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=None,
                message="没有订单 ID，未同步业务后台。",
            )
        if not self.oms.ready:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=order_id,
                message="OMS 未配置，未同步业务后台。",
            )

        data = self.oms.post(
            "/api/admin/orders/{order_id}/process",
            {"source": "agent", "resolution_note": note},
            order_id=order_id,
        )
        if isinstance(data, dict):
            code = data.get("code")
            payload_data = data.get("data")
            # Java 业务错误返回 HTTP200 + {code:400xx, data:null}，需判 code==200 且 data 非空才算成功；
            # 成功时 post 已把 data 解包为业务对象，不含 code/data 信封键，不命中以下判断。
            if (code is not None and code != 200) or ("data" in data and payload_data is None):
                logger.warning("mark_order_processed rejected by backend: %s", data)
                data = None
        if data is None:
            return BusinessSyncResult(
                attempted=True,
                success=False,
                order_id=order_id,
                message="已尝试同步业务后台，但接口未返回成功。",
            )

        return BusinessSyncResult(
            attempted=True,
            success=True,
            order_id=order_id,
            message="已自动同步业务后台，订单标记为已处理。",
        )

def unwrap_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        for key in ("data", "result", "customer", "order", "shipment"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return data
    return None


def first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _str_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def normalize_customer(data: dict[str, Any]) -> CustomerContext:
    return CustomerContext(
        customer_id=_str_id(first_value(data, "customer_id", "customerId", "userId", "id")),
        name=first_value(data, "name", "full_name", "fullName", "nickName", "nickname"),
        segment=first_value(data, "segment", "tier", "level", "customer_level", "customerLevel"),
        lifetime_value=to_float(first_value(data, "lifetime_value", "lifetimeValue", "ltv", "total_spend", "totalSpend")),
        risk_level=first_value(data, "risk_level", "riskLevel", "risk", "complaint_risk", "complaintRisk"),
    )


def normalize_order(data: dict[str, Any]) -> OrderContext:
    return OrderContext(
        order_id=_str_id(first_value(data, "order_id", "orderId", "orderNo", "id")),
        status=first_value(data, "status", "order_status", "orderStatus"),
        amount=to_float(first_value(data, "amount", "total_amount", "totalAmount", "pay_amount", "payAmount")),
        paid_at=first_value(data, "paid_at", "paidAt", "payment_time", "paymentTime", "created_at", "createdAt"),
        items=normalize_items(first_value(data, "items", "products", "goods", "order_items", "orderItems")),
        support_status=first_value(data, "support_status", "supportStatus", "service_status", "serviceStatus"),
        processed_at=first_value(data, "processed_at", "processedAt"),
        resolution_note=first_value(data, "resolution_note", "resolutionNote"),
    )


def normalize_shipment(data: dict[str, Any]) -> ShipmentContext:
    return ShipmentContext(
        order_id=_str_id(first_value(data, "order_id", "orderId", "orderNo")),
        carrier=first_value(data, "carrier", "company", "express_company", "expressCompany"),
        tracking_no=first_value(data, "tracking_no", "trackingNo", "tracking_number", "trackingNumber", "waybill_no", "waybillNo"),
        status=first_value(data, "status", "logistics_status", "logisticsStatus", "delivery_status", "deliveryStatus"),
        latest_event=first_value(data, "latest_event", "latestEvent", "latest_status", "latestStatus", "latest_trace", "latestTrace"),
        last_updated_at=first_value(data, "last_updated_at", "lastUpdatedAt", "update_time", "updateTime"),
    )


def normalize_items(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []

    items: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, dict):
            name = first_value(item, "name", "title", "sku_name", "skuName", "product_name", "productName")
            if name:
                items.append(str(name))
    return items


def normalize_product(data: dict[str, Any]) -> ProductContext:
    return ProductContext(
        product_id=_str_id(first_value(data, "product_id", "productId", "id")),
        name=first_value(data, "name", "title", "product_name", "productName"),
        price=to_float(first_value(data, "price", "unit_price", "unitPrice")),
        specs=first_value(data, "specs", "specifications", "spec"),
        description=first_value(data, "description", "desc", "detail"),
    )


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache
def get_external_gateway() -> ExternalSystemGateway:
    return ExternalSystemGateway()
