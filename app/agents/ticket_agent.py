from __future__ import annotations

import httpx
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Iterator
from uuid import uuid4

from app.core.config import get_settings
from app.models.schemas import (
    AnalysisRecord,
    ChatMessage,
    ChatRequest,
    ClassificationResult,
    ConversationData,
    ConversationStateEnum,
)
from app.agents.state import session_manager
from app.agents.tools import execute_tool, get_tool_descriptions
from app.agents.metrics import observe_llm_call, observe_llm_error, observe_llm_fallback, observe_tool_call, observe_conversation_end
from app.services.feedback_repository import feedback_repository
from app.services.prompt_registry import get_prompt_registry

logger = logging.getLogger(__name__)

MAX_REACT_STEPS = int(os.environ.get("SUPPORT_AGENT_MAX_REACT_STEPS", "10"))
REACT_LOOP_TIMEOUT = float(os.environ.get("SUPPORT_AGENT_REACT_LOOP_TIMEOUT", "120"))


class LLMUnavailableError(Exception):
    pass


SYSTEM_PROMPT_DEFAULT = """你是宠物店的智能客服助手，通过思考→行动→观察的循环来解决客户问题。

## 可用工具

{tool_descriptions}

## 工作规则

1. 每次对话你需要：①思考当前情况（Think）②选择要执行的操作（Action）
2. 输出格式必须是 JSON，包含 "thought"（思考过程）和 "action"（操作）字段
3. action 可以是以下之一：
   - {{"name": "工具名", "args": {{...}}}} — 调用工具
   - {{"name": "reply_user", "args": {{"message": "回复内容"}}}} — 直接回复用户
   - {{"name": "ask_user", "args": {{"question": "问题"}}}} — 需要更多信息时问用户
   - {{"name": "escalate_human", "args": {{"reason": "...", "summary": "..."}}}} — 转人工

## 关键约束

1. **退款必须转人工**：Agent 绝不直接处理退款
2. **先查信息再回复**：先查订单/物流/政策，确认后再回复
3. **信息不足先问用户**：缺少订单号或关键信息时，先用 ask_user
4. **最多 {max_steps} 步思考**：超过步数后强制转人工
5. **工具出错处理**：如果工具返回 error，最多重试一次
6. **回复必须基于事实**：不要编造信息"""


class ReActAgent:
    def __init__(self):
        self._llm_clients: dict[tuple[str | None, str], Any] = {}
        self._llm_was_used: bool = False

    def chat_stream(self, request: ChatRequest) -> Iterator[dict[str, Any]]:
        if request.conversation_id:
            raw = session_manager.peek(request.conversation_id)
            if raw is None:
                # 会话从未创建（如首次消息）：静默开启新会话，不提示已过期
                conv = session_manager.create(request.customer_id, request.order_id)
                generated = conv.conversation_id
                conv.conversation_id = request.conversation_id  # 保留前端指定的 id
                session_manager.invalidate(generated)  # 清除 create() 生成的僵尸 key
            elif raw.customer_id and request.customer_id and raw.customer_id != request.customer_id:
                # 跨用户访问：无论是否过期都拒绝
                yield {"event": "error", "message": "无权访问该会话"}
                return
            else:
                # 会话存在且非跨用户；load 返回 None 说明已过期
                existing = session_manager.load(request.conversation_id, request.customer_id)
                if existing is not None:
                    conv = existing
                else:
                    # 过期：允许开始新会话，但通知前端
                    yield {"event": "session_expired", "message": "会话已过期，已为您开启新的对话"}
                    conv = session_manager.create(request.customer_id, request.order_id)
                    generated = conv.conversation_id
                    conv.conversation_id = request.conversation_id  # 保留原 id，历史已丢
                    session_manager.invalidate(generated)  # 清除 create() 生成的僵尸 key
        else:
            conv = session_manager.create(request.customer_id, request.order_id)

        settings = get_settings()

        yield {"event": "session", "conversation_id": conv.conversation_id}

        if conv.status == ConversationStateEnum.WAITING_FOR_USER:
            yield from self._resume_conversation(conv, request)
            return

        if conv.status in {ConversationStateEnum.ESCALATED, ConversationStateEnum.RESOLVED}:
            yield {"event": "error", "message": "该会话已结束，请发起新的对话"}
            return

        conv.status = ConversationStateEnum.THINKING
        if request.live_data and not any(m.tool_name == "system_context" for m in conv.messages):
            conv.messages.append(ChatMessage(
                role="tool",
                content=json.dumps({"live_data": request.live_data}, ensure_ascii=False),
                tool_name="system_context",
                created_at=self._now(),
            ))
        conv.messages.append(ChatMessage(role="user", content=request.message, created_at=self._now()))
        if request.order_id and not conv.order_id:
            conv.order_id = request.order_id

        use_llm = settings.llm_enabled and settings.llm_api_key
        if not use_llm:
            yield {"event": "error", "message": "LLM 未配置，请检查设置"}
            return

        yield from self._react_loop(conv)

    def _get_or_create_conversation(self, request: ChatRequest) -> ConversationData:
        if request.conversation_id:
            conv = session_manager.load(request.conversation_id, request.customer_id)
            if conv:
                return conv
        conv = session_manager.create(request.customer_id, request.order_id)
        if request.conversation_id:
            generated = conv.conversation_id
            conv.conversation_id = request.conversation_id
            session_manager.invalidate(generated)  # 清除 create() 生成的僵尸 key
        return conv

    def _resume_conversation(self, conv: ConversationData, request: ChatRequest) -> Iterator[dict[str, Any]]:
        conv.status = ConversationStateEnum.THINKING
        conv.messages.append(ChatMessage(role="tool", content=request.message, tool_name="user_response", created_at=self._now()))
        yield {"event": "user_response", "content": request.message}
        yield from self._react_loop(conv)

    def _react_loop(self, conv: ConversationData) -> Iterator[dict[str, Any]]:
        settings = get_settings()
        step_count = 0
        loop_start = time.monotonic()
        tool_names = {t.name for t in self._get_tool_defs()}

        while step_count < MAX_REACT_STEPS:
            step_count += 1
            if time.monotonic() - loop_start > REACT_LOOP_TIMEOUT:
                yield {"event": "thought", "content": f"处理超时（>120s），自动转人工", "step": step_count}
                yield self._escalate_event("超过 120s 限制，自动转人工")
                yield {"event": "completed", "status": "escalated"}
                observe_conversation_end("timeout")
                self._append_escalation_reason(conv, "超过 120s 限制，自动转人工")
                self._persist_conversation(conv)
                return
            llm_result = self._call_llm(conv, settings)
            if llm_result is None:
                yield self._escalate_event("LLM 响应异常，已转人工处理")
                self._append_escalation_reason(conv, "LLM 响应异常")
                self._finalize_escalated(conv, "LLM 响应异常")
                yield {"event": "completed", "status": "escalated"}
                return

            thought = llm_result.get("thought", "")
            action = llm_result.get("action")
            conv.thought_chain.append(thought)
            yield {"event": "thought", "content": thought, "step": step_count}

            if not action or not isinstance(action, dict):
                yield self._escalate_event("Agent 决策异常，已转人工处理")
                self._append_escalation_reason(conv, "Agent 决策异常")
                self._finalize_escalated(conv, "Agent 决策异常")
                yield {"event": "completed", "status": "escalated"}
                return

            action_name = action.get("name", "")
            action_args = action.get("args", {})

            if action_name == "reply_user":
                message = action_args.get("message", "")
                conv.messages.append(ChatMessage(role="assistant", content=message, created_at=self._now()))
                conv.status = ConversationStateEnum.RESOLVED
                session_manager.save(conv)
                yield {"event": "reply", "content": message}
                yield {"event": "completed", "status": "resolved"}
                observe_conversation_end("resolved")
                self._persist_conversation(conv)
                return

            if action_name == "ask_user":
                question = action_args.get("question", "")
                conv.pending_tool = None
                conv.status = ConversationStateEnum.WAITING_FOR_USER
                session_manager.save(conv)
                yield {"event": "ask_user", "content": question}
                yield {"event": "pause"}
                return

            if action_name == "escalate_human":
                reason = action_args.get("reason", "无原因")
                summary = action_args.get("summary", "")
                conv.status = ConversationStateEnum.ESCALATED
                session_manager.save(conv)
                yield self._escalate_event(reason, summary)
                yield {"event": "completed", "status": "escalated"}
                observe_conversation_end("escalated")
                self._append_escalation_reason(conv, reason, summary)
                self._persist_conversation(conv)
                return

            if action_name in tool_names:
                yield {"event": "tool_call", "tool": action_name, "args": action_args, "step": step_count}
                t0 = time.monotonic()
                result = execute_tool(action_name, action_args, caller_customer_id=conv.customer_id)
                elapsed = time.monotonic() - t0
                observe_tool_call(action_name, elapsed, result.status)
                logger.info("Tool call: %s status=%s latency=%.2fs", action_name, result.status, elapsed)
                yield {"event": "tool_result", "tool": action_name, "result": result.to_dict()}
                conv.messages.append(ChatMessage(role="tool", content=json.dumps(result.to_dict(), ensure_ascii=False), tool_name=action_name, created_at=self._now()))
                continue

            yield {"event": "thought", "content": f"未知操作 {action_name}，尝试重新决策...", "step": step_count}

        conv.status = ConversationStateEnum.ESCALATED
        session_manager.save(conv)
        yield self._escalate_event(f"超过 {MAX_REACT_STEPS} 步限制，自动转人工")
        yield {"event": "completed", "status": "escalated"}
        observe_conversation_end("max_steps_exceeded")
        self._append_escalation_reason(conv, f"超过 {MAX_REACT_STEPS} 步限制，自动转人工")
        self._persist_conversation(conv)

    def _call_llm(self, conv: ConversationData, settings) -> dict[str, Any] | None:
        from langchain_openai import ChatOpenAI

        primary_kwargs = {
            "api_key": settings.llm_api_key,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 0,
            "temperature": 0.3,
        }
        if settings.llm_base_url and settings.llm_base_url.strip():
            primary_kwargs["base_url"] = settings.llm_base_url
        primary_llm = ChatOpenAI(model=settings.llm_model, **primary_kwargs)

        if settings.backup_llm_ready:
            backup_kwargs = {
                "api_key": settings.backup_llm_api_key,
                "timeout": settings.backup_llm_timeout_seconds,
                "max_retries": 0,
                "temperature": 0.3,
            }
            if settings.backup_llm_base_url and settings.backup_llm_base_url.strip():
                backup_kwargs["base_url"] = settings.backup_llm_base_url
            backup_llm = ChatOpenAI(model=settings.backup_llm_model, **backup_kwargs)
            llm = primary_llm.with_fallbacks([backup_llm])
        else:
            llm = primary_llm

        result = self._try_llm_call(llm, settings.llm_model, conv, settings)
        if result is not None:
            return result

        if settings.backup_llm_ready:
            observe_llm_fallback(settings.backup_llm_model)

        return None

    def _try_llm_call(self, llm: Any, model: str, conv: ConversationData, settings) -> dict[str, Any] | None:
        registry = get_prompt_registry()
        system_text = registry.load_prompt()
        messages = [
            {"role": "system", "content": system_text.format(
                tool_descriptions=get_tool_descriptions(),
                max_steps=MAX_REACT_STEPS,
            )}
        ]

        self._build_context_messages(conv, messages, settings)

        messages.append({
            "role": "user",
            "content": "请根据对话历史思考下一步操作，输出 JSON 格式 {\"thought\": \"...\", \"action\": {\"name\": \"...\", \"args\": {...}}}"
        })

        t0 = time.monotonic()
        try:
            bound_llm = llm.bind(
                response_format={"type": "json_object"},
                max_tokens=settings.llm_max_tokens,
            )
            response = bound_llm.invoke(messages)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.warning("LLM call failed for model %s after %.2fs: %s", model, elapsed, exc)
            observe_llm_error(model)
            return None

        elapsed = time.monotonic() - t0
        usage_metadata = response.response_metadata.get("token_usage", {})
        tokens_info = {
            "prompt_tokens": usage_metadata.get("prompt_tokens", 0),
            "completion_tokens": usage_metadata.get("completion_tokens", 0),
            "total_tokens": usage_metadata.get("total_tokens", 0),
        }

        prompt_t = tokens_info.get("prompt_tokens", 0)
        completion_t = tokens_info.get("completion_tokens", 0)
        cost = (prompt_t / 1000) * settings.cost_per_1k_prompt_tokens + (completion_t / 1000) * settings.cost_per_1k_completion_tokens

        observe_llm_call(model, elapsed, tokens_info, cost)
        logger.info("LLM call: model=%s latency=%.2fs prompt=%d completion=%d total=%d cost=$%.6f",
                    model, elapsed,
                    tokens_info.get("prompt_tokens", 0),
                    tokens_info.get("completion_tokens", 0),
                    tokens_info.get("total_tokens", 0),
                    cost)

        content = response.content
        if not content:
            logger.warning("LLM returned empty content for model %s", model)
            return None
        return self._parse_json_response(content.strip())

    def _build_context_messages(self, conv: ConversationData, messages: list[dict],
                                settings) -> None:
        try:
            import tiktoken
            enc = tiktoken.get_encoding(settings.llm_encoding_model)
            budget = int(settings.llm_context_token_limit * 0.8)
        except Exception:
            self._build_context_messages_chars(conv, messages, settings)
            return

        total_tokens = 0
        recent_messages = []
        for msg in reversed(conv.messages):
            entry = self._msg_to_entry(msg)
            tokens = len(enc.encode(entry.get("content", "")))
            if total_tokens + tokens > budget and recent_messages:
                break
            total_tokens += tokens
            recent_messages.insert(0, entry)

        if recent_messages and len(conv.messages) > len(recent_messages):
            summary_msg = f"[对话已折叠 {len(conv.messages) - len(recent_messages)} 条历史消息]"
            messages.append({"role": "user", "content": summary_msg})

        messages.extend(recent_messages)

    def _msg_to_entry(self, msg) -> dict:
        content = msg.content or ""
        if len(content) > 3000:
            content = content[:3000]
        if msg.role == "tool":
            return {
                "role": "user",
                "content": f"[工具 {msg.tool_name} 返回]\n{content[:2500]}",
            }
        elif msg.role == "assistant":
            return {"role": "assistant", "content": content[:3000]}
        else:
            return {"role": "user", "content": content[:3000]}

    def _build_context_messages_chars(self, conv: ConversationData, messages: list[dict],
                                      settings) -> None:
        total_chars = 0
        char_budget = settings.llm_context_token_limit * 2

        recent_messages = []
        for msg in reversed(conv.messages):
            content = msg.content or ""
            if len(content) > 2000:
                content = content[:2000]

            if msg.role == "tool":
                entry = {
                    "role": "user",
                    "content": f"[工具 {msg.tool_name} 返回]\n{content[:1500]}",
                }
            elif msg.role == "assistant":
                entry = {"role": "assistant", "content": content[:2000]}
            else:
                entry = {"role": "user", "content": content[:2000]}

            serialized = json.dumps(entry, ensure_ascii=False)
            entry_chars = len(serialized) + 2
            if total_chars + entry_chars > char_budget and recent_messages:
                break
            total_chars += entry_chars
            recent_messages.insert(0, entry)

        if recent_messages and len(conv.messages) > len(recent_messages):
            summary_msg = f"[对话已折叠 {len(conv.messages) - len(recent_messages)} 条历史消息，当前显示最近 {len(recent_messages)} 条]"
            messages.append({"role": "user", "content": summary_msg})

        messages.extend(recent_messages)

    def _parse_json_response(self, content: str) -> dict[str, Any] | None:
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse LLM JSON: %s", exc)
            return None

    def _get_tool_defs(self):
        from app.agents.tools import TOOLS
        return TOOLS

    def _escalate_event(self, reason: str, summary: str = "") -> dict[str, Any]:
        return {"event": "escalate", "reason": reason, "summary": summary}

    def _append_escalation_reason(self, conv: ConversationData, reason: str, summary: str = "") -> None:
        conv.messages.append(ChatMessage(
            role="tool",
            content=json.dumps({"data": {"reason": reason, "summary": summary}}, ensure_ascii=False),
            tool_name="escalate_human",
            created_at=self._now(),
        ))

    def _finalize_escalated(self, conv: ConversationData, reason: str) -> None:
        conv.status = ConversationStateEnum.ESCALATED
        session_manager.save(conv)
        self._persist_conversation(conv)

    def _sync_to_business_api(self, conv: ConversationData, ticket_id: str) -> None:
        try:
            settings = get_settings()
            if not settings.oms_api_base_url:
                return
            messages_json = json.dumps(
                [m.model_dump(mode="json") for m in conv.messages],
                ensure_ascii=False,
            )
            first_msg = conv.messages[0].content if conv.messages else ""
            last_msg = conv.messages[-1].content if conv.messages else ""
            tool_calls = []
            for m in conv.messages:
                if m.role == "tool" and m.tool_name:
                    tool_calls.append({"tool": m.tool_name, "content": m.content})
            cd = conv.classification_data or {}
            payload = {
                "ticket_id": ticket_id,
                "customer_id": conv.customer_id,
                "order_id": conv.order_id,
                "message": first_msg,
                "category": cd.get("category", "other"),
                "priority": cd.get("priority", "normal"),
                "confidence": cd.get("confidence", 0.0),
                "reply_source": "react_agent",
                "reply_draft": last_msg,
                "should_escalate": conv.status == ConversationStateEnum.ESCALATED,
                "escalation_reason": self._extract_escalation_reason(conv),
                "business_sync_success": False,
                "estimated_minutes_saved": 0,
                "conversation_id": conv.conversation_id,
                "thought_chain": json.dumps(conv.thought_chain, ensure_ascii=False),
                "tool_calls": json.dumps(tool_calls, ensure_ascii=False),
                "full_messages": messages_json,
            }
            if not settings.support_agent_token:
                logger.warning("support_agent_token 未配置，跳过回写业务后台")
                return
            url = f"{settings.oms_api_base_url.rstrip('/')}/api/admin/agent-analyses"
            headers = {"Authorization": f"Bearer {settings.support_agent_token}"}
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=5)
                if resp.status_code >= 400:
                    logger.warning("回写业务后台失败: status=%s body=%s", resp.status_code, resp.text[:200])
                else:
                    logger.info("回写业务后台成功: status=%s", resp.status_code)
            except Exception as exc:
                logger.warning("回写业务后台异常: %s", exc)
        except Exception as exc:
            logger.warning("Failed to sync to business API: %s", exc)

    def _extract_escalation_reason(self, conv: ConversationData) -> str:
        for msg in reversed(conv.messages):
            if msg.role == "tool" and msg.tool_name == "escalate_human":
                try:
                    data = json.loads(msg.content)
                    if isinstance(data, dict):
                        return data.get("data", {}).get("reason", "转人工") or "转人工"
                except (json.JSONDecodeError, AttributeError):
                    pass
        return "转人工"

    def _persist_conversation(self, conv: ConversationData, ticket_id: str | None = None) -> None:
        first_msg = conv.messages[0].content if conv.messages else ""
        last_msg = conv.messages[-1].content if conv.messages else ""
        cd = conv.classification_data or {}
        if not ticket_id:
            ticket_id = f"T-{uuid4().hex[:8].upper()}"
        record = AnalysisRecord(
            ticket_id=ticket_id,
            customer_id=conv.customer_id,
            order_id=conv.order_id,
            message=first_msg,
            category=cd.get("category", "other"),
            priority=cd.get("priority", "normal"),
            confidence=cd.get("confidence", 0.0),
            reply_draft=last_msg,
            should_escalate=conv.status == ConversationStateEnum.ESCALATED,
            prompt_version="v1",
            source="react_agent",
            created_at=datetime.now().isoformat(),
        )
        try:
            feedback_repository.save_analysis(record)
        except Exception as exc:
            logger.warning("Failed to persist conversation: %s", exc)
        self._sync_to_business_api(conv, ticket_id)

    def run_sync(
        self,
        message: str,
        customer_id: str | None = None,
        order_id: str | None = None,
        history: list[dict] | None = None,
        classification: ClassificationResult | None = None,
        live_data: dict | None = None,
        ticket_id: str | None = None,
    ) -> dict:
        settings = get_settings()
        if not settings.llm_enabled or not settings.llm_api_key:
            raise LLMUnavailableError("LLM not configured")

        conv = session_manager.create(customer_id, order_id)
        conv.ticket_id = ticket_id
        if customer_id:
            conv.messages.append(ChatMessage(
                role="tool",
                content=f"已知客户ID：{customer_id}",
                tool_name="system_context",
                created_at=self._now(),
            ))
        if order_id:
            conv.messages.append(ChatMessage(
                role="tool",
                content=f"已知订单号：{order_id}",
                tool_name="system_context",
                created_at=self._now(),
            ))
        if history:
            for h in history[-10:]:
                role = h.get("role", "user")
                content = h.get("content", "")
                if content:
                    conv.messages.append(ChatMessage(role=role, content=content, created_at=self._now()))
        if live_data:
            conv.messages.append(ChatMessage(
                role="tool",
                content=json.dumps({"live_data": live_data}, ensure_ascii=False),
                tool_name="system_context",
                created_at=self._now(),
            ))
        if classification:
            conv.classification_data = classification.model_dump()
            conv.messages.append(ChatMessage(
                role="tool",
                content=f"客户问题分类：{classification.category}，优先级：{classification.priority}",
                tool_name="classification",
                created_at=self._now(),
            ))
        conv.messages.append(ChatMessage(role="user", content=message, created_at=self._now()))

        tool_names = {t.name for t in self._get_tool_defs()}
        step_count = 0
        loop_start = time.monotonic()
        while step_count < MAX_REACT_STEPS:
            step_count += 1
            if time.monotonic() - loop_start > REACT_LOOP_TIMEOUT:
                self._append_escalation_reason(conv, "超过 120s 限制")
                return self._sync_result("处理超时，已转人工客服跟进", True,
                    "超过 120s 限制")
            llm_result = self._call_llm(conv, settings)
            if llm_result is None:
                raise LLMUnavailableError("LLM call failed after primary + backup")

            thought = llm_result.get("thought", "")
            action = llm_result.get("action")
            conv.thought_chain.append(thought)
            if not action or not isinstance(action, dict):
                self._append_escalation_reason(conv, "Agent 决策异常")
                return self._sync_result("系统异常，已转人工客服跟进", True, "Agent 决策异常")

            action_name = action.get("name", "")
            action_args = action.get("args", {})

            if action_name == "reply_user":
                reply = action_args.get("message", "") or "感谢您的咨询。"
                return self._sync_result(reply, False, None)

            if action_name == "ask_user":
                question = action_args.get("question", "")
                return self._sync_result(question, True, "需要更多信息，等待用户补充")

            if action_name == "escalate_human":
                reason = action_args.get("reason", "无原因")
                self._append_escalation_reason(conv, reason)
                return self._sync_result("已转人工客服跟进", True, reason)

            if action_name in tool_names:
                logger.info("LLM action: %s args: %s", action_name, action_args)
                result = execute_tool(action_name, action_args, caller_customer_id=customer_id)
                conv.messages.append(ChatMessage(
                    role="tool", content=json.dumps(result.to_dict(), ensure_ascii=False),
                    tool_name=action_name, created_at=self._now(),
                ))
                if result.status == "error":
                    self._append_escalation_reason(conv, f"工具 {action_name} 重试后仍失败")
                    return self._sync_result("获取信息失败，已转人工客服跟进", True, f"工具 {action_name} 重试后仍失败")
                continue

        self._append_escalation_reason(conv, f"超过 {MAX_REACT_STEPS} 步限制")
        return self._sync_result("处理超时，已转人工客服跟进", True, f"超过 {MAX_REACT_STEPS} 步限制")

    def _sync_result(self, reply: str, should_escalate: bool, reason: str | None) -> dict:
        return {
            "reply": reply,
            "should_escalate": should_escalate,
            "escalation_reason": reason,
            "source": "react_agent",
        }

    def _now(self) -> str:
        return datetime.now().isoformat()


agent = ReActAgent()
