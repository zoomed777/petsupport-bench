from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Category = Literal[
    "refund",
    "logistics",
    "account",
    "product",
    "complaint",
    "invoice",
    "other",
]

Priority = Literal["low", "normal", "high", "urgent"]
ReplySource = Literal["llm", "template", "structured_llm", "react_agent"]


class TicketAnalyzeRequest(BaseModel):
    ticket_id: str | None = Field(default=None, description="Optional ticket identifier for persistence.")
    customer_id: str | None = Field(default=None, description="Optional customer identifier.")
    message: str = Field(min_length=1, description="Raw customer ticket text.")
    order_id: str | None = None
    live_data: dict | None = Field(default=None, description="Inline live data pre-fetched by Java consumer to avoid round-trip.")
    history: list[dict] | None = Field(default=None, description="Conversation history for multi-turn: [{role:'user'|'assistant', content:'...'}]")


class CustomerContext(BaseModel):
    customer_id: str | None = None
    name: str | None = None
    segment: str | None = None
    lifetime_value: float | None = None
    risk_level: str | None = None


class OrderContext(BaseModel):
    order_id: str | None = None
    status: str | None = None
    amount: float | None = None
    paid_at: str | None = None
    items: list[str] = []
    support_status: str | None = None
    processed_at: str | None = None
    resolution_note: str | None = None


class ShipmentContext(BaseModel):
    order_id: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    status: str | None = None
    latest_event: str | None = None
    last_updated_at: str | None = None


class ProductContext(BaseModel):
    product_id: str | None = None
    name: str | None = None
    price: float | None = None
    specs: str | None = None
    description: str | None = None


class ExternalContext(BaseModel):
    customer: CustomerContext | None = None
    order: OrderContext | None = None
    shipment: ShipmentContext | None = None
    product: ProductContext | None = None


class BusinessSyncResult(BaseModel):
    attempted: bool = False
    success: bool = False
    order_id: str | None = None
    message: str


class KnowledgeHit(BaseModel):
    id: str
    title: str
    category: Category
    score: float
    answer: str
    retrieval_method: str = "rag_vector"


class ClassificationResult(BaseModel):
    category: Category
    priority: Priority
    confidence: float = Field(ge=0, le=1)
    matched_keywords: list[str]
    risk_flags: list[str]


class StructuredDecision(BaseModel):
    category: Category | None = None
    priority: Priority | None = None
    reply: str = Field(min_length=1)
    should_escalate: bool
    confidence: float = Field(default=0.7, ge=0, le=1)
    risk_flags: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None
    reasoning: str | None = None

    @field_validator("reply")
    @classmethod
    def reply_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reply must not be blank")
        return stripped


class TicketAnalysis(BaseModel):
    ticket_id: str
    customer_id: str | None
    order_id: str | None
    message: str
    external_context: ExternalContext | None = None
    classification: ClassificationResult
    knowledge_hits: list[KnowledgeHit]
    reply_draft: str
    reply_source: ReplySource
    structured_decision: StructuredDecision | None = None
    should_escalate: bool
    escalation_reason: str | None
    business_sync: BusinessSyncResult | None = None
    analysis_record_sync: BusinessSyncResult | None = None
    estimated_minutes_saved: float
    business_value: dict[str, float | int | str]


class EvaluationResult(BaseModel):
    total_tickets: int
    classification_accuracy: float
    auto_handle_rate: float
    escalation_rate: float
    total_minutes_saved: float
    avg_minutes_saved_per_ticket: float
    high_risk_tickets: int
    category_distribution: dict[str, int]


class DashboardResult(BaseModel):
    summary: EvaluationResult
    recent_analyses: list[TicketAnalysis]


class FeedbackRequest(BaseModel):
    ticket_id: str
    original_reply: str
    revised_reply: str
    category: Category
    accepted: bool = True
    editor: str | None = None
    notes: str | None = None


class FeedbackRecord(FeedbackRequest):
    id: int
    created_at: str


class FeedbackMetrics(BaseModel):
    total_feedback: int
    acceptance_rate: float
    avg_revision_ratio: float
    by_category: dict[str, int]


class LLMConfigStatus(BaseModel):
    enabled: bool
    ready: bool
    model: str
    base_url_set: bool
    api_key_set: bool


class AnalysisRecord(BaseModel):
    ticket_id: str
    customer_id: str | None = None
    order_id: str | None = None
    message: str
    category: Category
    priority: Priority
    confidence: float
    reply_draft: str
    should_escalate: bool
    source: ReplySource
    prompt_version: str = "v1"
    user_satisfied: bool | None = None  # None=未评价, True=满意, False=不满意
    created_at: str


class ExternalIntegrationStatus(BaseModel):
    crm_ready: bool
    oms_ready: bool
    logistics_ready: bool
    crm_path_template: str
    oms_path_template: str
    logistics_path_template: str


# ========== ReAct Agent 对话模型 ==========

class ConversationStateEnum(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WAITING_FOR_USER = "waiting"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    status: str = "pending"  # pending | success | error


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    created_at: str = ""


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    customer_id: str | None = None
    order_id: str | None = None
    live_data: dict | None = None


class ConversationData(BaseModel):
    conversation_id: str
    status: ConversationStateEnum = ConversationStateEnum.IDLE
    messages: list[ChatMessage] = Field(default_factory=list)
    customer_id: str | None = None
    order_id: str | None = None
    external_context: ExternalContext | None = None
    pending_tool: ToolCall | None = None
    thought_chain: list[str] = Field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""
    summary: dict[str, Any] | None = None
    classification_data: dict[str, Any] | None = None
    ticket_id: str | None = None
