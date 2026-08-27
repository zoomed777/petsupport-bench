"""多视角 LLM-as-Judge（第 2 层评测）。

三个独立视角的 Hy3 Judge 对 D4（事实准确与证据可追溯）、
D6（可执行性）打分，并对 D5 做语义复核：

  - safety_auditor   安全审查员：专注红线与危险建议
  - owner_view       主人视角：作为普通养宠人能否看懂、照做
  - fact_checker     事实核查员：逐条断言比对知识库

设计依据：单一 Judge 存在自我偏好与位置偏差，三视角独立打分后
取中位数可显著降低方差；分差 > 1 时标记冲突，交由人工仲裁。

所有 prompt 固化于 prompts/judge_*.md，保证评测可复现。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

JUDGE_PERSPECTIVES = ["safety_auditor", "owner_view", "fact_checker"]


@dataclass
class JudgeVerdict:
    perspective: str
    d4_score: int          # 0-4
    d6_score: int          # 0-4
    d5_override: Optional[list] = None   # 语义复核发现的红线违规（None=维持规则层结论）
    rationale: str = ""


class JudgeEnsemble:
    """三视角 Judge 集成。"""

    def __init__(self) -> None:
        self.client = OpenAI(
            base_url=os.environ["EVAL_JUDGE_BASE_URL"],
            api_key=os.environ.get("EVAL_JUDGE_API_KEY", "EMPTY"),
        )
        self.model = os.environ.get("EVAL_JUDGE_MODEL", "hy3")

    def _ask(self, prompt_file: str, variables: dict) -> str:
        with open(prompt_file, encoding="utf-8") as f:
            template = f.read()
        for k, v in variables.items():
            template = template.replace("{{" + k + "}}", str(v))
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": template}],
            temperature=0.0,  # 评测场景固定温度，降低波动
        )
        return resp.choices[0].message.content or ""

    def judge(self, case: dict, output: dict) -> list[JudgeVerdict]:
        """对单条输出运行三视角评测。返回各视角判定。"""
        verdicts: list[JudgeVerdict] = []
        for perspective in JUDGE_PERSPECTIVES:
            raw = self._ask(
                f"prompts/judge_{perspective}.md",
                {"case": case.get("user_message", ""), "output": output.get("text", "")},
            )
            verdicts.append(self._parse(perspective, raw))
        return verdicts

    @staticmethod
    def _parse(perspective: str, raw: str) -> JudgeVerdict:
        """解析 Judge 输出（约定 JSON：{d4, d6, d5_override, rationale}）。"""
        import json

        try:
            data = json.loads(raw)
            return JudgeVerdict(
                perspective=perspective,
                d4_score=int(data["d4"]),
                d6_score=int(data["d6"]),
                d5_override=data.get("d5_override"),
                rationale=data.get("rationale", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return JudgeVerdict(
                perspective=perspective,
                d4_score=-1,  # 解析失败标记，由流程重试
                d6_score=-1,
                rationale=f"解析失败: {raw[:100]}",
            )


def median_score(scores: list[int]) -> tuple[int, bool]:
    """三视角取中位数。返回 (中位数, 是否存在冲突需人工仲裁)。"""
    valid = [s for s in scores if s >= 0]
    if not valid:
        return -1, True
    valid.sort()
    mid = valid[len(valid) // 2]
    conflict = (max(valid) - min(valid)) > 1
    return mid, conflict
