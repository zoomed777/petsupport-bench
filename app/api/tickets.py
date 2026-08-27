from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
import httpx
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from app.agents.ticket_agent import ReActAgent, LLMUnavailableError
from app.agents.state import session_manager
from app.agents.tools import TOOLS, TOOL_HANDLERS, execute_tool
from app.core.config import get_settings
from app.models.schemas import (
    Category,
    ChatRequest,
    ClassificationResult,
    EvaluationResult,
    ExternalIntegrationStatus,
    ExternalContext,
    FeedbackMetrics,
    FeedbackRecord,
    FeedbackRequest,
    LLMConfigStatus,
    TicketAnalyzeRequest,
    ConversationStateEnum,
)
from app.models.schemas import AnalysisRecord
from app.services.external_systems import get_external_gateway
from app.services.feedback_repository import feedback_repository
from app.services.knowledge_base import get_knowledge_base
from app.services.reply_generator import generate_reply
from app.services.ticket_classifier import classify_ticket

logger = logging.getLogger(__name__)

router = APIRouter()

_kb_lock = asyncio.Lock()


async def _save_kb_safe(kb, articles: list[dict]):
    async with _kb_lock:
        def _do_save():
            kb.path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
            kb.rebuild()
        await asyncio.to_thread(_do_save)


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    react_agent = ReActAgent()

    def generate():
        try:
            for event in react_agent.chat_stream(request):
                yield format_sse(event)
        except Exception as exc:
            yield format_sse({"event": "error", "message": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream; charset=utf-8")


@router.post("/tickets/analyze")
async def analyze_ticket(request: TicketAnalyzeRequest) -> dict:
    classification = classify_ticket(request.message)

    try:
        agent = ReActAgent()
        result = agent.run_sync(
            message=request.message,
            customer_id=request.customer_id,
            order_id=request.order_id,
            history=request.history,
            classification=classification,
            live_data=request.live_data,
            ticket_id=request.ticket_id,
        )
        reply = result["reply"]
        reply_source = "react_agent"
        should_escalate = result["should_escalate"]
        escalation_reason = result.get("escalation_reason")
        prompt_version = result.get("prompt_version", "v1")
    except LLMUnavailableError:
        kb = get_knowledge_base()
        hits = kb.search(request.message, classification.category)
        reply = generate_reply(request.message, classification, hits)
        reply_source = "template"
        should_escalate = _assist_escalation(classification, bool(hits), reply_source)
        escalation_reason = "大模型不可用，使用模板兜底" if should_escalate else None
        prompt_version = "template"

    estimated_minutes_saved = 0.0 if should_escalate else 5.0

    result = {
        "classification": classification.model_dump(mode="json"),
        "reply_draft": reply,
        "reply_source": reply_source,
        "should_escalate": should_escalate,
        "escalation_reason": escalation_reason,
        "estimated_minutes_saved": estimated_minutes_saved,
        "prompt_version": prompt_version,
    }

    try:
        ticket_id = request.ticket_id or f"T-{int(time.time())}"
        record = AnalysisRecord(
            ticket_id=ticket_id,
            customer_id=request.customer_id,
            order_id=request.order_id,
            message=request.message,
            category=classification.category,
            priority=classification.priority,
            confidence=classification.confidence,
            reply_draft=reply,
            should_escalate=should_escalate,
            source=reply_source,
            prompt_version=prompt_version,
            created_at=datetime.now().isoformat(),
        )
        feedback_repository.save_analysis(record, full_response=json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        logger.warning("Failed to persist analysis: %s", exc)

    return result


@router.post("/evaluate", response_model=EvaluationResult)
async def evaluate_sample_tickets() -> EvaluationResult:
    tickets = load_sample_tickets(get_settings().sample_tickets_path)
    total = len(tickets)
    correct = 0
    total_auto = 0
    cat_results: dict[str, dict] = {}
    results: list[dict] = []

    for item in tickets:
        msg = item["message"]
        expected = item.get("expected_category", "other")
        kb = get_knowledge_base()
        classification = classify_ticket(msg)
        hits = kb.search(msg, classification.category)
        generate_reply(msg, classification, hits)

        is_correct = classification.category == expected
        if is_correct:
            correct += 1
        should_escalate = _assist_escalation(classification, bool(hits))
        if not should_escalate:
            total_auto += 1

        if expected not in cat_results:
            cat_results[expected] = {"total": 0, "correct": 0}
        cat_results[expected]["total"] += 1
        if is_correct:
            cat_results[expected]["correct"] += 1

        results.append({
            "index": len(results),
            "message": msg[:60],
            "expected": expected,
            "predicted": classification.category,
            "correct": is_correct,
            "confidence": round(classification.confidence, 3),
            "should_escalate": should_escalate,
        })

    accuracy = correct / total if total else 0
    auto_rate = total_auto / total if total else 0

    return EvaluationResult(
        total_tickets=total,
        classification_accuracy=round(accuracy, 4),
        auto_handle_rate=round(auto_rate, 4),
        escalation_rate=round(1 - auto_rate, 4),
        total_minutes_saved=0,
        avg_minutes_saved_per_ticket=0,
        high_risk_tickets=0,
        category_distribution={k: v["total"] for k, v in sorted(cat_results.items())},
    )


@router.get("/integrations/context", response_model=ExternalContext)
async def get_integration_context(customer_id: str | None = None, order_id: str | None = None) -> ExternalContext:
    return get_external_gateway().get_context(customer_id, order_id)


@router.post("/feedback/revisions", response_model=FeedbackRecord)
async def save_feedback(request: FeedbackRequest) -> FeedbackRecord:
    return feedback_repository.save(request)


@router.get("/feedback/revisions", response_model=list[FeedbackRecord])
async def list_feedback(limit: int = 20) -> list[FeedbackRecord]:
    return feedback_repository.list_recent(limit=limit)


@router.get("/feedback/metrics", response_model=FeedbackMetrics)
async def get_feedback_metrics() -> FeedbackMetrics:
    return feedback_repository.metrics()


@router.get("/analyses")
async def list_analyses(page: int = 1, size: int = 20) -> dict:
    items, total = feedback_repository.list_analyses(limit=size, offset=(page - 1) * size)
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: int) -> dict:
    item = feedback_repository.get_analysis_by_id(analysis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return item


@router.get("/tickets/{ticket_id}/conversation")
async def get_ticket_conversation(ticket_id: str) -> list[dict]:
    items = feedback_repository.list_analyses_by_ticket(ticket_id)
    return items


@router.get("/metrics/satisfaction")
async def get_satisfaction_metrics() -> dict:
    return feedback_repository.satisfaction_metrics().model_dump()


@router.get("/metrics/prompt-comparison")
async def get_prompt_comparison() -> dict:
    return {"versions": {}, "by_version": {}}


@router.get("/config/llm", response_model=LLMConfigStatus)
async def get_llm_config() -> LLMConfigStatus:
    settings = get_settings()
    return LLMConfigStatus(
        enabled=settings.llm_enabled,
        ready=settings.llm_ready,
        model=settings.llm_model,
        base_url_set=bool(settings.llm_base_url),
        api_key_set=bool(settings.llm_api_key),
    )


@router.get("/config/integrations", response_model=ExternalIntegrationStatus)
async def get_external_integration_config() -> ExternalIntegrationStatus:
    settings = get_settings()
    return ExternalIntegrationStatus(
        crm_ready=settings.crm_ready,
        oms_ready=settings.oms_ready,
        logistics_ready=settings.logistics_ready,
        crm_path_template=settings.crm_customer_path_template,
        oms_path_template=settings.oms_order_path_template,
        logistics_path_template=settings.logistics_shipment_path_template,
    )


@router.get("/mall-tickets")
async def list_mall_tickets(status: str = "all", page: int = 1, size: int = 50) -> dict:
    settings = get_settings()
    base = settings.crm_api_base_url
    if not base:
        return {"total": 0, "records": []}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{base}/api/tickets", params={"status": status, "page": page, "size": size})
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") == 200:
            return body["data"]
        return {"total": 0, "records": []}


@router.get("/kb/articles")
async def list_kb_articles(category: str | None = None) -> list[dict]:
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    articles = kb.articles
    if category:
        articles = [a for a in articles if a.get("category") == category]
    return articles


class KBArticleCreate(BaseModel):
    policy_id: str
    category: Category = "other"
    title: str
    condition: str = ""
    content: str
    keywords: list[str] = []
    type: str = "policy"


@router.post("/kb/articles")
async def create_kb_article(article: KBArticleCreate) -> dict:
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    articles = list(kb.articles)
    articles.append(article.model_dump())
    await _save_kb_safe(kb, articles)
    return {"success": True, "policy_id": article.policy_id}


@router.put("/kb/articles/{policy_id}")
async def update_kb_article(policy_id: str, article: KBArticleCreate) -> dict:
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    articles = list(kb.articles)
    for i, a in enumerate(articles):
        if a.get("policy_id") == policy_id:
            articles[i] = article.model_dump()
            await _save_kb_safe(kb, articles)
            return {"success": True}
    raise HTTPException(status_code=404, detail="Article not found")


@router.delete("/kb/articles/{policy_id}")
async def delete_kb_article(policy_id: str) -> dict:
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    articles = list(kb.articles)
    new_articles = [a for a in articles if a.get("policy_id") != policy_id]
    if len(new_articles) == len(articles):
        raise HTTPException(status_code=404, detail="Article not found")
    await _save_kb_safe(kb, new_articles)
    return {"success": True}


@router.post("/kb/reindex")
async def reindex_kb() -> dict:
    from app.services.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    kb.rebuild()
    return {"success": True}


def _assist_escalation(classification: ClassificationResult, has_hit: bool, source: str = "llm") -> bool:
    if classification.priority == "urgent":
        return True
    if classification.category == "complaint":
        return True
    if classification.confidence < 0.58:
        return True
    if not has_hit:
        return True
    if source == "template":
        return True
    return False


def load_sample_tickets(path: Path) -> list[dict]:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Sample data not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


class SatisfactionRequest(BaseModel):
    ticket_id: str
    analysis_id: int | None = None
    satisfied: bool


@router.post("/feedback/satisfaction")
async def submit_satisfaction(request: SatisfactionRequest) -> dict:
    try:
        feedback_repository.save_satisfaction(
            ticket_id=request.ticket_id,
            satisfied=request.satisfied,
        )
        return {"success": True}
    except Exception as exc:
        logger.warning("Failed to save satisfaction: %s", exc)
        return {"success": False, "error": str(exc)}


class AdminReplyRequest(BaseModel):
    message: str
    admin_id: str = "admin"


@router.get("/admin/conversations")
async def list_admin_conversations(limit: int = 50):
    settings = get_settings()
    base = settings.oms_api_base_url
    if not base:
        return {"total": 0, "records": []}
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{base}/api/admin/agent-analyses?limit={limit}")
        resp.raise_for_status()
        body = resp.json()
        return body


@router.get("/admin/conversations/{conversation_id}")
async def get_admin_conversation(conversation_id: str):
    conv = session_manager.load(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "conversation_id": conv.conversation_id,
        "status": conv.status.value,
        "customer_id": conv.customer_id,
        "order_id": conv.order_id,
        "messages": [m.model_dump(mode="json") for m in conv.messages],
        "thought_chain": conv.thought_chain,
        "created_at": conv.created_at,
        "expires_at": conv.expires_at,
    }


@router.post("/admin/conversations/{conversation_id}/reply")
async def admin_reply_to_conversation(conversation_id: str, request: AdminReplyRequest):
    conv = session_manager.load(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    from app.models.schemas import ChatMessage
    conv.messages.append(ChatMessage(
        role="assistant",
        content=f"[管理员 {request.admin_id} 回复]\n{request.message}",
        created_at=datetime.now().isoformat(),
    ))
    if conv.status == ConversationStateEnum.ESCALATED:
        conv.status = ConversationStateEnum.WAITING_FOR_USER
    conv.expires_at = ""
    session_manager.save(conv)
    return {"success": True, "message": "管理员回复已注入，用户可继续对话"}


@router.get("/admin/conversation-page", response_class=HTMLResponse)
async def admin_conversation_page():
    return ADMIN_CONVERSATION_HTML


def format_sse(payload: dict) -> str:
    return f"data: {json.dumps(to_jsonable(payload), ensure_ascii=False)}\n\n"


def to_jsonable(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


ADMIN_CONVERSATION_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>转人工对话 · 管理员</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; background: #f0f2f5; color: #172033;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    }
    header {
      background: #fff; border-bottom: 1px solid #dce3ea;
      position: sticky; top: 0; z-index: 10;
    }
    .shell { width: min(1080px, calc(100vw - 32px)); margin: 0 auto; }
    .topbar {
      min-height: 64px; display: flex; align-items: center;
      justify-content: space-between; gap: 16px;
    }
    h1 { margin: 0; font-size: 22px; }
    .sub { color: #667085; font-size: 13px; }
    main { padding: 20px 0 40px; }
    .queue {
      display: flex; flex-direction: column; gap: 12px;
    }
    .conv-card {
      background: #fff; border: 1px solid #dce3ea;
      border-radius: 10px; padding: 16px; cursor: pointer;
      transition: box-shadow .15s;
    }
    .conv-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.08); }
    .conv-card.open { border-color: #0f766e; box-shadow: 0 0 0 2px rgba(15,118,110,.2); }
    .conv-header {
      display: flex; justify-content: space-between; align-items: center;
    }
    .conv-header h3 { margin: 0; font-size: 16px; }
    .badge {
      display: inline-flex; min-height: 24px; align-items: center;
      border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700;
    }
    .badge.urgent { background: #fef2f2; color: #b91c1c; }
    .badge.normal { background: #fff7ed; color: #b45309; }
    .badge.success { background: #ecfdf3; color: #027a48; }
    .conv-detail { margin-top: 14px; display: none; }
    .conv-card.open .conv-detail { display: block; }
    .chat {
      display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px;
    }
    .msg {
      padding: 10px 14px; border-radius: 8px; max-width: 85%;
      line-height: 1.5; font-size: 14px;
    }
    .msg.user { background: #dbeafe; align-self: flex-end; }
    .msg.assistant { background: #d1fae5; align-self: flex-start; }
    .msg.tool { background: #f3f4f6; align-self: flex-start; font-family: monospace; font-size: 13px; }
    .msg.thought { background: #fef3c7; align-self: flex-start; font-style: italic; font-size: 13px; }
    .msg .meta { font-size: 11px; color: #667085; margin-top: 4px; }
    .admin-reply-box { margin-top: 12px; border-top: 1px solid #dce3ea; padding-top: 12px; }
    .admin-reply-box textarea {
      width: 100%; min-height: 80px; border: 1px solid #dce3ea;
      border-radius: 6px; padding: 10px; font: inherit; font-size: 14px;
    }
    .admin-reply-box button {
      margin-top: 8px; border: 0; border-radius: 6px;
      background: #0f766e; color: #fff; padding: 9px 16px;
      font-weight: 700; cursor: pointer;
    }
    .admin-reply-box button:disabled { background: #98a2b3; cursor: default; }
    .empty { color: #667085; text-align: center; padding: 40px; }
    a { color: #0f766e; font-weight: 700; text-decoration: none; }
    @media (max-width: 700px) {
      .msg { max-width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div class="shell topbar">
      <div>
        <h1>转人工对话 · 管理员</h1>
        <div class="sub">Agent 转人工后，管理员可在此查看完整对话并回复用户</div>
      </div>
      <div style="display:flex;gap:10px">
        <a href="http://127.0.0.1:8011/admin">订单后台</a>
        <button onclick="loadConversations()" style="background:#344054;color:#fff;border:0;border-radius:6px;padding:9px 12px;font-weight:700;cursor:pointer">刷新</button>
      </div>
    </div>
  </header>
  <main class="shell">
    <div class="queue" id="queue">
      <div class="empty">加载中...</div>
    </div>
  </main>
  <script>
    async function loadConversations() {
      const res = await fetch("/api/admin/conversations?limit=50");
      const payload = await res.json();
      const items = payload.data || [];
      const el = document.querySelector("#queue");
      if (!items.length) {
        el.innerHTML = '<div class="empty">暂无转人工对话记录。</div>';
        return;
      }
      el.innerHTML = items.map((item) => {
        const isEscalated = item.should_escalate;
        const pri = item.priority === "urgent" ? "urgent" : "normal";
        const statusLabel = item.final_reply ? "已处理" : (isEscalated ? "待处理" : "自动");
        const statusCls = item.final_reply ? "success" : (isEscalated ? "urgent" : "normal");
        return `
          <div class="conv-card" onclick="toggleConv(event, '${item.conversation_id || item.ticket_id}')" data-id="${item.conversation_id || item.ticket_id}">
            <div class="conv-header">
              <div>
                <h3>${item.ticket_id}</h3>
                <div class="sub">${item.customer_id || "-"} · ${item.order_id || "-"} · ${item.created_at}</div>
              </div>
              <div>
                <span class="badge ${statusCls}">${statusLabel}</span>
                <span class="badge ${pri}">${item.category} / ${item.priority}</span>
              </div>
            </div>
            <div class="conv-detail">
              <div class="chat" id="chat-${item.conversation_id || item.ticket_id}">
                <div class="sub">加载完整对话...</div>
              </div>
            </div>
          </div>
        `;
      }).join("");
    }

    async function toggleConv(event, convId) {
      const card = event.currentTarget;
      card.classList.toggle("open");
      if (card.classList.contains("open")) {
        const chatEl = document.querySelector("#chat-" + convId);
        if (chatEl && chatEl.querySelector(".sub")) {
          await loadConversationDetail(convId, chatEl);
        }
      }
    }

    async function loadConversationDetail(convId, chatEl) {
      try {
        const res = await fetch("/api/admin/conversations/" + convId);
        if (!res.ok) {
          chatEl.innerHTML = '<div class="sub">对话已过期或不存在（可能已从 Redis 中移除）</div>';
          return;
        }
        const data = await res.json();
        const msgs = data.messages || [];
        const thoughts = data.thought_chain || [];
        let html = "";
        let thoughtIdx = 0;
        for (const msg of msgs) {
          if (msg.role === "user" && msg.tool_name !== "user_response") {
            html += '<div class="msg user">' + escapeHtml(msg.content) + '<div class="meta">用户</div></div>';
          } else if (msg.role === "assistant") {
            html += '<div class="msg assistant">' + escapeHtml(msg.content) + '<div class="meta">Agent</div></div>';
          } else if (msg.role === "tool") {
            if (thoughtIdx < thoughts.length) {
              html += '<div class="msg thought">🤔 ' + escapeHtml(thoughts[thoughtIdx]) + '</div>';
              thoughtIdx++;
            }
            const toolName = msg.tool_name || "unknown";
            let content = msg.content || "";
            if (content.length > 300) content = content.slice(0, 300) + "...";
            html += '<div class="msg tool">🔧 ' + escapeHtml(toolName) + '<br>' + escapeHtml(content) + '</div>';
          } else if (msg.role === "tool" && msg.tool_name === "user_response") {
            html += '<div class="msg user">' + escapeHtml(msg.content) + '<div class="meta">用户</div></div>';
          }
        }
        const status = data.status;
        const canReply = status === "escalated" || status === "waiting";
        if (canReply) {
          html += '<div class="admin-reply-box">';
          html += '<textarea id="reply-' + convId + '" placeholder="输入管理员回复..."></textarea>';
          html += '<button onclick="sendAdminReply(\'' + convId + '\')">回复用户</button>';
          html += '<span id="reply-status-' + convId + '" style="margin-left:10px;color:#667085;font-size:13px"></span>';
          html += '</div>';
        } else {
          html += '<div class="sub" style="margin-top:8px">状态: ' + status + '（不可回复）</div>';
        }
        chatEl.innerHTML = html;
      } catch (e) {
        chatEl.innerHTML = '<div class="sub">加载失败: ' + e.message + '</div>';
      }
    }

    async function sendAdminReply(convId) {
      const ta = document.querySelector("#reply-" + convId);
      const status = document.querySelector("#reply-status-" + convId);
      const msg = ta ? ta.value.trim() : "";
      if (!msg) { status.textContent = "请输入回复内容"; return; }
      status.textContent = "发送中...";
      try {
        const res = await fetch("/api/admin/conversations/" + convId + "/reply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg, admin_id: "admin" }),
        });
        if (res.ok) {
          status.textContent = "✅ 已发送，用户可继续对话";
          ta.value = "";
          loadConversationDetail(convId, document.querySelector("#chat-" + convId));
        } else {
          const err = await res.json();
          status.textContent = "❌ " + (err.detail || "发送失败");
        }
      } catch (e) {
        status.textContent = "❌ 网络错误";
      }
    }

    function escapeHtml(v) {
      if (!v) return "";
      return String(v).replace(/[&<>"']/g, function(c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c];
      });
    }

    loadConversations();
  </script>
</body>
</html>
"""
