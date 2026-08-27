from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def observe_llm_call(model: str, duration: float, tokens: dict[str, int] | None = None,
                     cost_usd: float = 0.0) -> None:
    logger.info("LLM call: model=%s latency=%.2fs cost=$%.6f", model, duration, cost_usd)


def observe_llm_error(model: str) -> None:
    logger.warning("LLM error: model=%s", model)


def observe_llm_fallback(model: str) -> None:
    logger.warning("LLM fallback triggered")


def observe_tool_call(tool: str, duration: float, status: str) -> None:
    logger.info("Tool call: %s status=%s latency=%.2fs", tool, status, duration)


def observe_conversation_end(status: str) -> None:
    logger.info("Conversation ended: status=%s", status)
