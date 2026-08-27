from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.models.schemas import ExternalContext

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    rewritten: str
    intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    category_hint: str = "other"
    confidence: float = 0.0
    priority: str = "normal"


class QueryRewriter:
    def rewrite(self, message: str, context: ExternalContext | None = None) -> IntentResult:
        settings = get_settings()
        if not settings.llm_enabled or not settings.llm_api_key:
            return IntentResult(rewritten=message, intent="unknown", category_hint="other")

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )

            context_str = context.model_dump_json() if context else ""

            response = client.chat.completions.create(
                model=settings.llm_model,
                temperature=0.1,
                max_tokens=400,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是宠物店客服工单理解助手。将客户问题改写为清晰、完整的查询语句，并输出JSON。\n"
                            "字段说明:\n"
                            "- rewritten: 改写后的查询文本（标准化、无歧义、包含关键实体）\n"
                            "- intent: 意图标签（order_inquiry, refund_request, logistics_inquiry, product_inquiry, complaint, invoice_request, account_issue, return_exchange, cancel_order, other）\n"
                            "- entities: 从问题中提取的实体字典（如 order_id、product_name、amount 等）\n"
                            "- category: 工单分类（refund, logistics, account, product, complaint, invoice, other）\n"
                            "- priority: 优先级（low, normal, high, urgent）\n"
                            "- confidence: 对该理解的确信度 0-1"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"客户问题: {message}\n" + (f"\n已知上下文: {context_str[:800]}" if context_str else ""),
                    },
                ],
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                raise ValueError("empty response")

            data = json.loads(content)
            raw_cat = str(data.get("category", "other"))
            cat = raw_cat if raw_cat in {"refund", "logistics", "account", "product", "complaint", "invoice", "other"} else "other"
            raw_pri = str(data.get("priority", "normal"))
            pri = raw_pri if raw_pri in {"low", "normal", "high", "urgent"} else "normal"
            return IntentResult(
                rewritten=data.get("rewritten", message),
                intent=data.get("intent", "unknown"),
                entities=data.get("entities", {}),
                category_hint=cat,
                confidence=float(data.get("confidence", 0.5)),
                priority=pri,
            )
        except Exception:
            logger.warning("LLM query rewriting failed; using original message", exc_info=True)
            return IntentResult(rewritten=message, intent="unknown", category_hint="other")


query_rewriter = QueryRewriter()
