from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.tools import tool

from app.services.external_systems import get_external_gateway
from app.services.knowledge_base import get_knowledge_base
from app.services.query_rewriter import query_rewriter

logger = logging.getLogger(__name__)

_acting_for_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("acting_for", default=None)


@dataclass
class ToolResult:
    status: str = "ok"
    data: Any = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "data": self.data, "error_message": self.error_message}


@tool
def get_order(order_id: str) -> dict:
    """查询订单信息，返回订单状态、金额、商品列表等"""
    if not order_id:
        return {"status": "error", "error_message": "缺少订单号 order_id"}
    try:
        gateway = get_external_gateway()
        order_data = gateway.oms.get(f"/orders/{order_id}", acting_for=_acting_for_ctx.get())
        if order_data is None:
            return {"status": "error", "error_message": f"未找到订单 {order_id}"}
        return {"status": "ok", "data": order_data}
    except Exception as exc:
        logger.warning("get_order failed: %s", exc)
        return {"status": "error", "error_message": f"查询订单超时或失败: {exc}"}


@tool
def get_shipment(order_id: str) -> dict:
    """查询物流信息，返回承运商、运单号、物流状态和最新节点"""
    if not order_id:
        return {"status": "error", "error_message": "缺少订单号 order_id"}
    try:
        gateway = get_external_gateway()
        shipment_data = gateway.logistics.get(f"/shipments/by-order/{order_id}", acting_for=_acting_for_ctx.get())
        if shipment_data is None:
            return {"status": "error", "error_message": f"未找到订单 {order_id} 的物流信息"}
        return {"status": "ok", "data": shipment_data}
    except Exception as exc:
        logger.warning("get_shipment failed: %s", exc)
        return {"status": "error", "error_message": f"查询物流超时或失败: {exc}"}


@tool
def get_customer(customer_id: str) -> dict:
    """查询客户信息，返回客户等级、风险等级、历史消费等"""
    if not customer_id:
        return {"status": "error", "error_message": "缺少客户ID customer_id"}
    try:
        gateway = get_external_gateway()
        customer_data = gateway.crm.get(f"/customers/{customer_id}", acting_for=_acting_for_ctx.get())
        if customer_data is None:
            return {"status": "error", "error_message": f"未找到客户 {customer_id}"}
        return {"status": "ok", "data": customer_data}
    except Exception as exc:
        logger.warning("get_customer failed: %s", exc)
        return {"status": "error", "error_message": f"查询客户信息超时或失败: {exc}"}


@tool
def search_policy(query: str, category: Optional[str] = None) -> dict:
    """搜索售后政策规则，按关键词匹配退货、退款、物流等政策"""
    if not query:
        return {"status": "error", "error_message": "缺少搜索关键词 query"}
    try:
        rewritten = query_rewriter.rewrite(query)
        final_query = rewritten.rewritten if rewritten.rewritten != query else query
        kb = get_knowledge_base()
        hits = kb.search(final_query, category, limit=3)
        if not hits:
            return {"status": "ok", "data": {"hits": [], "message": "未找到匹配的政策规则"}}
        return {"status": "ok", "data": {"hits": [h.model_dump(mode="json") for h in hits]}}
    except Exception as exc:
        logger.warning("search_policy failed: %s", exc)
        return {"status": "error", "error_message": f"搜索政策规则失败: {exc}"}


@tool
def ask_user(question: str) -> dict:
    """向用户提问，收集更多信息（如订单号、问题描述等）"""
    if not question:
        return {"status": "error", "error_message": "缺少提问内容 question"}
    return {"status": "ok", "data": {"question": question}}


@tool
def escalate_human(reason: str, summary: str) -> dict:
    """将问题转交人工客服处理。调用后本次会话结束，系统生成交接摘要"""
    if not reason or not summary:
        return {"status": "error", "error_message": "转人工需要提供 reason 和 summary"}
    return {"status": "ok", "data": {"reason": reason, "summary": summary, "escalated": True}}


@tool
def reply_user(message: str) -> dict:
    """直接回复用户。当所有信息已齐备、无需再调用其他工具时，用此工具回复用户"""
    if not message:
        return {"status": "error", "error_message": "回复内容不能为空"}
    return {"status": "ok", "data": {"message": message}}


@tool
def get_flash_activities() -> dict:
    """查询当前正在进行的和即将开始的秒杀活动，返回活动列表"""
    try:
        gateway = get_external_gateway()
        activities = gateway.get_flash_activities()
        if not activities:
            return {"status": "ok", "data": {"message": "当前没有进行中或即将开始的秒杀活动。"}}
        return {"status": "ok", "data": {"activities": activities}}
    except Exception as exc:
        logger.warning("get_flash_activities failed: %s", exc)
        return {"status": "error", "error_message": f"查询秒杀活动失败: {exc}"}


@tool
def search_products(keyword: str) -> dict:
    """按关键词搜索商品，返回匹配的商品列表（含商品名、价格、库存）"""
    if not keyword:
        return {"status": "error", "error_message": "缺少搜索关键词 keyword"}
    try:
        gateway = get_external_gateway()
        products = gateway.search_products(keyword)
        if not products:
            return {"status": "ok", "data": {"message": f"未找到与「{keyword}」相关的商品。"}}
        return {"status": "ok", "data": {"products": products, "keyword": keyword}}
    except Exception as exc:
        logger.warning("search_products failed: %s", exc)
        return {"status": "error", "error_message": f"搜索商品失败: {exc}"}


@tool
def get_group_activities() -> dict:
    """查询当前进行中的拼团活动列表，返回拼团活动信息（含商品、拼团价、人数要求）"""
    try:
        gateway = get_external_gateway()
        activities = gateway.get_group_activities()
        if not activities:
            return {"status": "ok", "data": {"message": "当前没有进行中的拼团活动。"}}
        return {"status": "ok", "data": {"activities": activities}}
    except Exception as exc:
        logger.warning("get_group_activities failed: %s", exc)
        return {"status": "error", "error_message": f"查询拼团活动失败: {exc}"}


@tool
def check_slots(date: str, service_type: str) -> dict:
    """查询指定日期和服务的可用预约时段。date格式为YYYY-MM-DD，service_type可选：BATH/CHECKUP/VACCINE/NEUTER/GROOMING（对应 洗澡/检查/疫苗/绝育/美容）"""
    if not date or not service_type:
        return {"status": "error", "error_message": "缺少 date 或 service_type"}
    try:
        gateway = get_external_gateway()
        slots = gateway.get_appointment_slots(date, service_type, acting_for=_acting_for_ctx.get())
        if not slots:
            return {"status": "ok", "data": {"message": f"{date} {service_type}暂无可用时段", "slots": []}}
        return {"status": "ok", "data": {"slots": slots, "date": date, "service_type": service_type}}
    except Exception as exc:
        logger.warning("check_slots failed: %s", exc)
        return {"status": "error", "error_message": f"查询可用时段失败: {exc}"}


@tool
def create_booking(user_id: str, service_type: str, appoint_date: str, appoint_time: str, pet_id: Optional[str] = None, remark: Optional[str] = None) -> dict:
    """为宠物创建服务预约。service_type必须为 BATH/CHECKUP/VACCINE/NEUTER/GROOMING 之一（对应 洗澡/检查/疫苗/绝育/美容）；appoint_date格式为YYYY-MM-DD；appoint_time必须为check_slots返回的时段字符串（格式如09:00-10:00）。预约后状态为PENDING，需到店确认"""
    if not user_id or not service_type or not appoint_date or not appoint_time:
        return {"status": "error", "error_message": "缺少必填参数: user_id, service_type, appoint_date, appoint_time"}
    try:
        payload = {
            "userId": user_id,
            "serviceType": service_type,
            "appointDate": appoint_date,
            "appointTime": appoint_time,
        }
        if pet_id:
            payload["petId"] = pet_id
        if remark:
            payload["remark"] = remark
        gateway = get_external_gateway()
        result = gateway.create_appointment(payload, acting_for=_acting_for_ctx.get())
        if result is None:
            return {"status": "error", "error_message": "预约创建失败，请稍后再试或联系人工"}
        return {"status": "ok", "data": {"appointment": result, "message": f"{service_type}预约成功：{appoint_date} {appoint_time}"}}
    except Exception as exc:
        logger.warning("create_booking failed: %s", exc)
        return {"status": "error", "error_message": f"创建预约失败: {exc}"}


@tool
def search_pet_care(query: str, species: Optional[str] = None) -> dict:
    """搜索宠物养护知识，查询疫苗、驱虫、喂养、常见病等宠物护理信息"""
    if not query:
        return {"status": "error", "error_message": "缺少搜索关键词 query"}
    try:
        kb = get_knowledge_base()
        hits = kb.search(query, limit=3)
        if not hits:
            return {"status": "ok", "data": {"message": "未找到相关养宠知识，建议转人工咨询", "hits": []}}
        return {"status": "ok", "data": {"hits": [h.model_dump(mode="json") for h in hits]}}
    except Exception as exc:
        logger.warning("search_pet_care failed: %s", exc)
        return {"status": "error", "error_message": f"搜索养宠知识失败: {exc}"}


@tool
def get_point_info(customer_id: str, order_id: Optional[str] = None) -> dict:
    """查询用户积分余额和积分商城可兑换商品列表。如果提供了订单号(order_id)，还会返回该订单可获得的积分。"""
    if not customer_id:
        return {"status": "error", "error_message": "缺少客户ID customer_id"}
    try:
        gateway = get_external_gateway()
        info = gateway.get_point_info(customer_id, order_id, acting_for=_acting_for_ctx.get())
        if not info:
            return {"status": "ok", "data": {"message": "未查询到积分信息。"}}
        return {"status": "ok", "data": info}
    except Exception as exc:
        logger.warning("get_point_info failed: %s", exc)
        return {"status": "error", "error_message": f"查询积分信息失败: {exc}"}


TOOLS = [
    get_order,
    get_shipment,
    get_customer,
    search_policy,
    search_pet_care,
    check_slots,
    create_booking,
    ask_user,
    escalate_human,
    reply_user,
    get_flash_activities,
    search_products,
    get_group_activities,
    get_point_info,
]

TOOL_HANDLERS: dict[str, Any] = {t.name: t for t in TOOLS}

RETRY_TOOLS = {
    "get_order", "get_shipment", "get_customer", "get_point_info",
    "get_flash_activities", "search_products", "get_group_activities",
    "search_pet_care", "check_slots",
}


def _invoke_tool(handler, args: dict[str, Any]) -> ToolResult:
    result = handler.invoke(args)
    if isinstance(result, dict):
        return ToolResult(
            status=result.get("status", "ok"),
            data=result.get("data"),
            error_message=result.get("error_message"),
        )
    return ToolResult("ok", result)


def execute_tool(tool_name: str, args: dict[str, Any], caller_customer_id: Optional[str] = None) -> ToolResult:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return ToolResult("error", None, f"未知工具: {tool_name}")
    if caller_customer_id:
        # Data isolation: prevent customer from acting on other customers' data
        args_key = {
            "get_customer": "customer_id",
            "get_point_info": "customer_id",
            "create_booking": "user_id",
        }.get(tool_name)
        if args_key:
            target_id = args.get(args_key)
            if target_id and target_id != caller_customer_id:
                logger.warning("Isolation blocked: tool=%s caller=%s target=%s", tool_name, caller_customer_id, target_id)
                return ToolResult("error", None, "无权操作他人数据")
    _acting_for_ctx.set(caller_customer_id)
    try:
        result = _invoke_tool(handler, args)
        if result.status == "error" and tool_name in RETRY_TOOLS:
            logger.info("Tool %s failed, retrying once...", tool_name)
            result = _invoke_tool(handler, args)
            if result.status == "error":
                logger.warning("Retry %s still failed: %s", tool_name, result.error_message)
        return result
    except TypeError as exc:
        return ToolResult("error", None, f"工具 {tool_name} 参数错误: {exc}")
    except Exception as exc:
        logger.exception("Tool %s failed", tool_name)
        return ToolResult("error", None, f"工具 {tool_name} 执行异常: {exc}")
    finally:
        _acting_for_ctx.set(None)


def get_tool_descriptions() -> str:
    lines: list[str] = []
    for t in TOOLS:
        name = t.name
        desc = t.description
        try:
            schema = t.args_schema.schema() if t.args_schema else {}
        except Exception:
            schema = {}
        params = json.dumps(schema, ensure_ascii=False, indent=2)
        lines.append(f"## {name}\n{desc}\n参数:\n{params}")
    return "\n\n".join(lines)
