"""意图路由：区分订单/商品/健康类消息。

规则层：关键词初判（快速、零成本）。
LLM 层（可选）：边界样本交由 Hy3 判定——规则返回 "mixed" 或低置信时触发。

路由原则（重要）：**健康优先**。混合意图消息（订单闲聊裹急症）必须
进入健康分诊管线，订单问题在报告中附带处理，绝不能因订单内容
挤占健康风险的优先级——这正是评测样本 MIX-001 检验的行为。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.triage_schemas import Intent

_ORDER_KEYWORDS = [
    "订单", "发货", "物流", "快递", "退换", "退款", "退货", "取消订单",
    "订单号", "什么时候到", "还没到", "没收到", "签收", "运费", "优惠券",
    "积分", "签到", "拼团", "秒杀", "预约", "核销",
]
_PRODUCT_KEYWORDS = [
    "猫粮", "狗粮", "猫砂", "罐头", "有货吗", "库存", "价格", "多少钱",
    "尺寸", "型号", "推荐一款", "适合多大的", "成分",
]
_HEALTH_KEYWORDS = [
    "呕吐", "拉稀", "腹泻", "软便", "不吃", "不喝", "没精神", "蔫",
    "喘", "呼吸", "抽搐", "抖", "误食", "吃了", "舔了", "吞了",
    "尿", "便血", "流血", "伤口", "疫苗", "驱虫", "绝育", "生病",
    "不吃不喝", "精神不好", "肚子胀", "咳嗽", "打喷嚏", "眼睛红",
]


@dataclass
class RouteVerdict:
    intent: Intent
    confidence: float           # 规则层置信度
    matched: dict[str, list[str]]   # 各类命中的关键词

    @property
    def needs_health_pipeline(self) -> bool:
        return self.intent in (Intent.health, Intent.mixed)


def classify(message: str) -> RouteVerdict:
    """关键词规则路由。同时命中订单与健康 → mixed（健康优先）。"""
    matched = {
        "order": [k for k in _ORDER_KEYWORDS if k in message],
        "product": [k for k in _PRODUCT_KEYWORDS if k in message],
        "health": [k for k in _HEALTH_KEYWORDS if k in message],
    }
    has_order = bool(matched["order"] or matched["product"])
    has_health = bool(matched["health"])

    if has_health and has_order:
        return RouteVerdict(Intent.mixed, 0.9, matched)
    if has_health:
        # 健康关键词越多置信度越高
        conf = min(0.6 + 0.1 * len(matched["health"]), 0.95)
        return RouteVerdict(Intent.health, conf, matched)
    if has_order:
        if matched["order"]:
            return RouteVerdict(Intent.order, 0.8, matched)
        return RouteVerdict(Intent.product, 0.6, matched)
    # 无命中：无法判定，保守起见交由健康管线兜底（LLM 层再细化）
    return RouteVerdict(Intent.health, 0.3, matched)


def classify_with_fallback(message: str, llm_classify=None) -> RouteVerdict:
    """规则 + LLM 两级路由。规则置信度 < 0.5 时调用 LLM 复核。"""
    verdict = classify(message)
    if verdict.confidence < 0.5 and llm_classify is not None:
        try:
            refined = llm_classify(message)
            if refined in ("order", "product", "health", "mixed"):
                verdict.intent = Intent(refined)
                verdict.confidence = 0.85
        except Exception:  # noqa: BLE001 — LLM 不可用时退回规则结果
            pass
    return verdict
