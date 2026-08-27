from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "Customer Support Agent"
    baseline_minutes_per_ticket: float = 5.0
    agent_review_minutes_per_ticket: float = 1.2
    auto_close_minutes_per_ticket: float = 0.4
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    feedback_db_path: Path = Path(__file__).resolve().parents[2] / "data" / "feedback.db"
    llm_enabled: bool = False
    llm_model: str = "qwen-plus"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 45.0
    llm_max_tokens: int = 800
    backup_llm_enabled: bool = False
    backup_llm_model: str = "gpt-4o-mini"
    backup_llm_base_url: str | None = None
    backup_llm_api_key: str | None = None
    backup_llm_timeout_seconds: float = 60.0
    prompts_dir: Path = Path(__file__).resolve().parents[2] / "app" / "agents" / "prompts"
    cost_per_1k_prompt_tokens: float = 0.00015
    cost_per_1k_completion_tokens: float = 0.00060
    llm_context_token_limit: int = 6000
    llm_encoding_model: str = "cl100k_base"
    external_api_timeout_seconds: float = 8.0
    react_loop_timeout_seconds: float = 120.0
    hf_hub_endpoint: str = "https://hf-mirror.com"

    @property
    def backup_llm_ready(self) -> bool:
        return self.backup_llm_enabled and bool(self.backup_llm_api_key)
    crm_api_base_url: str | None = None
    crm_api_token: str | None = None
    crm_customer_path_template: str = "/customers/{customer_id}"
    oms_api_base_url: str | None = None
    oms_api_token: str | None = None
    oms_order_path_template: str = "/orders/{order_id}"
    logistics_api_base_url: str | None = None
    logistics_api_token: str | None = None
    logistics_shipment_path_template: str = "/shipments/by-order/{order_id}"
    support_agent_token: str | None = None
    redis_url: str | None = "redis://localhost:6379/0"
    redis_session_ttl_seconds: int = 3600

    @property
    def llm_ready(self) -> bool:
        return self.llm_enabled and bool(self.llm_api_key)

    @property
    def crm_ready(self) -> bool:
        return bool(self.crm_api_base_url)

    @property
    def oms_ready(self) -> bool:
        return bool(self.oms_api_base_url)

    @property
    def logistics_ready(self) -> bool:
        return bool(self.logistics_api_base_url)

    @property
    def knowledge_base_path(self) -> Path:
        return self.data_dir / "knowledge_base.json"

    @property
    def sample_tickets_path(self) -> Path:
        return self.data_dir / "sample_tickets.json"

    @property
    def customer_profiles_path(self) -> Path:
        return self.data_dir / "customer_profiles.json"

    @property
    def orders_path(self) -> Path:
        return self.data_dir / "orders.json"

    @property
    def shipments_path(self) -> Path:
        return self.data_dir / "shipments.json"


@lru_cache
def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parents[2]
    for env_path in (root_dir.parent / ".env", root_dir / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)

    explicit_enabled = os.getenv("SUPPORT_AGENT_LLM_ENABLED")
    api_key = (
        os.getenv("SUPPORT_AGENT_LLM_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    backup_api_key = os.getenv("SUPPORT_AGENT_BACKUP_LLM_API_KEY")
    return Settings(
        llm_enabled=explicit_enabled.lower() == "true" if explicit_enabled is not None else bool(api_key),
        llm_model=os.getenv("SUPPORT_AGENT_LLM_REPLY_MODEL", "qwen-plus"),
        llm_base_url=os.getenv("SUPPORT_AGENT_LLM_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("OPENAI_BASE_URL"),
        llm_api_key=api_key,
        llm_timeout_seconds=float(os.getenv("SUPPORT_AGENT_LLM_TIMEOUT_SECONDS", "45")),
        llm_max_tokens=int(os.getenv("SUPPORT_AGENT_LLM_MAX_TOKENS", "800")),
        backup_llm_enabled=os.getenv("SUPPORT_AGENT_BACKUP_LLM_ENABLED", "").lower() == "true",
        backup_llm_model=os.getenv("SUPPORT_AGENT_BACKUP_LLM_MODEL", "gpt-4o-mini"),
        backup_llm_base_url=os.getenv("SUPPORT_AGENT_BACKUP_LLM_BASE_URL"),
        backup_llm_api_key=backup_api_key,
        backup_llm_timeout_seconds=float(os.getenv("SUPPORT_AGENT_BACKUP_LLM_TIMEOUT_SECONDS", "60")),

        cost_per_1k_prompt_tokens=float(os.getenv("SUPPORT_AGENT_COST_PER_1K_PROMPT", "0.00015")),
        cost_per_1k_completion_tokens=float(os.getenv("SUPPORT_AGENT_COST_PER_1K_COMPLETION", "0.00060")),
        llm_context_token_limit=int(os.getenv("SUPPORT_AGENT_CONTEXT_TOKEN_LIMIT", "6000")),
        llm_encoding_model=os.getenv("SUPPORT_AGENT_ENCODING_MODEL", "cl100k_base"),
        external_api_timeout_seconds=float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "8")),
        react_loop_timeout_seconds=float(os.getenv("SUPPORT_AGENT_REACT_LOOP_TIMEOUT", "120")),
        hf_hub_endpoint=os.getenv("SUPPORT_AGENT_HF_ENDPOINT", "https://hf-mirror.com"),
        crm_api_base_url=os.getenv("CRM_API_BASE_URL"),
        crm_api_token=os.getenv("CRM_API_TOKEN"),
        crm_customer_path_template=os.getenv("CRM_CUSTOMER_PATH_TEMPLATE", "/customers/{customer_id}"),
        oms_api_base_url=os.getenv("OMS_API_BASE_URL"),
        oms_api_token=os.getenv("OMS_API_TOKEN"),
        oms_order_path_template=os.getenv("OMS_ORDER_PATH_TEMPLATE", "/orders/{order_id}"),
        logistics_api_base_url=os.getenv("LOGISTICS_API_BASE_URL"),
        logistics_api_token=os.getenv("LOGISTICS_API_TOKEN"),
        logistics_shipment_path_template=os.getenv(
            "LOGISTICS_SHIPMENT_PATH_TEMPLATE",
            "/shipments/by-order/{order_id}",
        ),
        support_agent_token=os.getenv("SUPPORT_AGENT_TOKEN"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_session_ttl_seconds=int(os.getenv("REDIS_SESSION_TTL_SECONDS", "3600")),
    )
