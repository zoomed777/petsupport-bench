from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class PromptRegistry:
    def __init__(self, prompts_dir: Path):
        self._text = self._load(prompts_dir)

    def _load(self, prompts_dir: Path) -> str:
        fpath = prompts_dir / "react.txt"
        if not fpath.exists():
            logger.warning("Prompt file not found at %s, using default", fpath)
            return self._default_prompt()
        text = fpath.read_text(encoding="utf-8")
        logger.info("Loaded prompt (%d chars)", len(text))
        return text

    def load_prompt(self) -> str:
        return self._text

    def _default_prompt(self) -> str:
        return (
            "你是宠物店的智能客服助手，通过思考\u2192行动\u2192观察的循环来解决客户问题。\n"
            "## 可用工具\n{tool_descriptions}\n"
            "## 工作规则\n"
            "1. 每次输出 JSON，包含 thought（思考）和 action（操作）\n"
            "2. 先查信息再回复，信息不足先问用户\n"
            "3. 退款必须转人工\n"
            "4. 最多 {max_steps} 步思考\n"
        )


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    return PromptRegistry(Path(__file__).resolve().parents[1] / "agents" / "prompts")
