"""Hy3 OpenAI-compatible client used by the triage application.

The client deliberately has a small surface area: it is optional for local
rule-only demos, and reads credentials only from environment variables.  No
credential is stored in source code or result files.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
import httpx
from openai import OpenAI


def _load_env() -> None:
    """Load the repository-local .env when the app is started directly."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)


def _json_object(raw: str) -> dict[str, Any]:
    """Accept plain JSON and the common fenced-JSON form returned by models."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Hy3 response must be a JSON object")
    return value


class Hy3TriageClient:
    """Small, explicit adapter for a Hy3-compatible chat-completions API."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60) -> None:
        # Some campus/corporate proxy configurations terminate the TokenHub TLS
        # tunnel.  TokenHub is called directly; no request content is routed via
        # an inherited HTTP(S)_PROXY setting.
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            http_client=httpx.Client(timeout=timeout, trust_env=False),
        )
        self.model = model

    @classmethod
    def from_env(cls) -> "Hy3TriageClient | None":
        _load_env()
        base_url = os.getenv("HY3_BASE_URL") or os.getenv("SUPPORT_AGENT_LLM_BASE_URL")
        api_key = os.getenv("HY3_API_KEY") or os.getenv("SUPPORT_AGENT_LLM_API_KEY")
        model = os.getenv("HY3_MODEL") or os.getenv("SUPPORT_AGENT_LLM_REPLY_MODEL", "hy3")
        if not base_url or not api_key:
            return None
        return cls(base_url, api_key, model, float(os.getenv("HY3_TIMEOUT_SECONDS", "60")))

    def _complete(self, system: str, user: str, temperature: float = 0.0) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def classify(self, message: str) -> str:
        raw = self._complete(
            "You classify Chinese pet-store support messages. Return JSON only: "
            '{"intent":"order|product|health|mixed"}. Health always wins in mixed messages.',
            message,
        )
        intent = str(_json_object(raw).get("intent", "")).lower()
        if intent not in {"order", "product", "health", "mixed"}:
            raise ValueError("invalid intent from Hy3")
        return intent

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate only editable report prose; deterministic guardrails retain control."""
        system = """You are a pet health communication assistant, not a veterinarian.
Return JSON only with keys risk, actions, warnings, vet_summary, uncertainty.
Never diagnose, prescribe, recommend human medicine, or provide dosage.
Use cautious, plain Chinese. Preserve the supplied triage level; do not lower an emergency.
Actions and warnings must be arrays of short strings. If information is insufficient, say so.
"""
        raw = self._complete(system, json.dumps(context, ensure_ascii=False))
        data = _json_object(raw)
        allowed: dict[str, Any] = {}
        for key in ("risk", "vet_summary", "uncertainty"):
            if isinstance(data.get(key), str):
                allowed[key] = data[key].strip()
        for key in ("actions", "warnings"):
            if isinstance(data.get(key), list) and all(isinstance(x, str) for x in data[key]):
                allowed[key] = [x.strip() for x in data[key] if x.strip()]
        return allowed
