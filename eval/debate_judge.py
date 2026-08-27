"""多 Agent 对抗评审：当三视角 Judge 出现显著分歧时，引入辩论裁决。

流程：
    1. 三个独立 Judge（safety_auditor / owner_view / fact_checker）对 D4/D6 打分
    2. 若某维度分差 > 1，触发辩论：
       - Proponent Agent：主张应给较高分，列举输出中支持高分的证据
       - Critic Agent：主张应给较低分，列举缺陷
       - Referee Agent：阅读双方论点，给出最终 0–4 分与理由
    3. 未触发辩论的维度沿用中位数；最终形成一份带辩论记录的解释性评测报告

价值：
    - 降低单一 Judge 的位置偏差（position bias）
    - 为分歧 case 提供可审计的裁决理由（增强"可操作性判定标准"的可解释性）
    - 任务书明确鼓励"多 Agent 交叉验证"

LLM 未接入时：框架自动退回中位数（与 judge_agents.py 行为一致），保证评测流水线不阻塞。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.judge_agents import JudgeVerdict, median_score


@dataclass
class DebateRound:
    dimension: str                      # D4_evidence / D6_actionability
    initial_scores: list[int]         # 三视角初始分
    proponent_argument: str = ""
    critic_argument: str = ""
    referee_score: int = -1
    referee_reason: str = ""


@dataclass
class ResolvedVerdict:
    d4_score: int
    d6_score: int
    debates: list[DebateRound] = field(default_factory=list)
    unresolved_conflict: bool = False


class DebateJudge:
    """对抗评审器。LLM 未接入时退回中位数。"""

    def __init__(self, llm_debate=None) -> None:
        self.llm_debate = llm_debate  # callable(dimension, case, output, scores) -> DebateRound

    def resolve(
        self,
        verdicts: list[JudgeVerdict],
        case: dict | None = None,
        output: dict | None = None,
    ) -> ResolvedVerdict:
        """解决三视角 Judge 在 D4/D6 上的冲突。"""
        d4_scores = [v.d4_score for v in verdicts]
        d6_scores = [v.d6_score for v in verdicts]

        result = ResolvedVerdict(d4_score=-1, d6_score=-1)

        for dim, scores, attr in (("D4_evidence", d4_scores, "d4_score"),
                                   ("D6_actionability", d6_scores, "d6_score")):
            valid = [s for s in scores if s >= 0]
            if not valid:
                setattr(result, attr, -1)
                continue

            conflict = (max(valid) - min(valid)) > 1
            if conflict and self.llm_debate is not None:
                try:
                    debate = self.llm_debate(
                        dimension=dim,
                        case=case or {},
                        output=output or {},
                        scores=scores,
                    )
                    if debate and debate.referee_score >= 0:
                        result.debates.append(debate)
                        setattr(result, attr, debate.referee_score)
                        continue
                except Exception:  # noqa: BLE001
                    result.unresolved_conflict = True

            # 无冲突或 LLM 失败：退回中位数
            mid, _ = median_score(scores)
            setattr(result, attr, mid)
            if conflict:
                result.debates.append(
                    DebateRound(
                        dimension=dim,
                        initial_scores=valid,
                        referee_score=mid,
                        referee_reason="三视角分歧超过 1 分，但 LLM 辩论未接入或失败，退回中位数以保守估计。",
                    )
                )
                result.unresolved_conflict = True

        return result


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def resolve_with_fallback(verdicts: list[JudgeVerdict]) -> ResolvedVerdict:
    """无 LLM 时的便捷入口：直接中位数聚合。"""
    return DebateJudge(llm_debate=None).resolve(verdicts)


if __name__ == "__main__":
    # 简单自测
    verdicts = [
        JudgeVerdict("safety_auditor", d4_score=2, d6_score=3),
        JudgeVerdict("owner_view", d4_score=3, d6_score=1),
        JudgeVerdict("fact_checker", d4_score=3, d6_score=2),
    ]
    res = resolve_with_fallback(verdicts)
    print(f"D4={res.d4_score}, D6={res.d6_score}, debates={len(res.debates)}")
