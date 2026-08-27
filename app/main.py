from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api import tickets
from app.core.config import get_settings
from app.core.log_config import setup_logging

setup_logging()

logger = logging.getLogger(__name__)


app = FastAPI(
    title="Customer Support Agent API",
    description="客服工单处理 Agent：分类、知识库检索、回复草稿、人工介入判断和量化指标。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router, prefix="/api", tags=["tickets"])

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_check():
    settings = get_settings()
    if settings.llm_ready:
        logger.info("LLM connected: model=%s base_url=%s", settings.llm_model, settings.llm_base_url or "(default)")
    else:
        logger.warning("LLM not configured (no API Key), will use template replies")
    logger.info("External integrations: CRM=%s OMS=%s Logistics=%s Redis=%s",
                "OK" if settings.crm_ready else "N/A",
                "OK" if settings.oms_ready else "N/A",
                "OK" if settings.logistics_ready else "N/A",
                "OK" if settings.redis_url else "N/A (in-memory fallback)")
