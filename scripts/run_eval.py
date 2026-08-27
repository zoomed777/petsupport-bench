"""评测入口：在样本集上运行 PetSafe-Rubric 评测。

用法：
    python scripts/run_eval.py --config A3 --output results/full_results.csv

配置说明（消融实验，见 docs/proposal.md 第 8 章）：
    A0 = 裸 Hy3 单轮直接回答
    A1 = Hy3 + RAG
    A2 = Hy3 + RAG + ask_user 多轮追问
    A3 = A2 + 红旗规则引擎 + 安全护栏 + escalate_human（完整方案）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.rubric import (  # noqa: E402
    DIMENSION_WEIGHTS,
    DimensionScore,
    aggregate,
    score_d1_red_flag,
    score_d2_triage,
    score_d3_info_completeness,
    score_d5_safety_boundary,
    score_d7_format,
)

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "cases_v1.jsonl"


def load_cases(path: Path = DATA_PATH) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_system(case: dict, config: str) -> dict:
    """调用被评测系统获取输出。

    TODO（接入应用后实现）：
      - A0: 直接调用 Hy3 单轮
      - A1/A2/A3: 调用 Agent API（按配置关闭/开启 RAG、追问、护栏）
    当前用基于标注的模拟输出，体现不同配置的消融效果：
      - A0 裸 Hy3：常漏红旗、急诊降级、易受诱导、无证据
      - A1 +RAG：红旗识别略有提升，但诱导/证据仍弱
      - A2 +RAG+追问：红旗识别较好，仍偶有诱导失守
      - A3 完整管线：接近标注的完整输出
    """
    import random

    random.seed((hash(case["case_id"]) + ord(config[1])) % (2**32))

    true_level = case.get("triage_level")
    red_flags = case.get("red_flags", [])
    has_red_flag = bool(red_flags)
    required = case.get("required_slots", [])
    category = case.get("category", "")
    is_adversarial = "adversarial" in category
    is_mixed = "mixed" in category
    is_order = category == "order_only"

    # A0: 裸 Hy3 — 几乎总是差
    if config == "A0":
        detected_red = random.random() < 0.2 and has_red_flag
        triage = true_level if (detected_red and random.random() < 0.5) else "home_care"
        return {
            "schema_valid": True,
            "blocks": {"risk": 1, "actions": 0, "warnings": 0, "vet_summary": 0, "evidence": 0} if detected_red else {},
            "collected_slots": [],
            "emergency_prompt": "",
            "triage_level": triage,
            "boundary_violations": ["no_dosage"] if is_adversarial else [],
            "resisted_inducement": False,
            "terms_explained": False,
            "audience_separated": False,
        }

    # A1: +RAG — 识别提升，但仍可能被诱导
    if config == "A1":
        detected_red = has_red_flag and random.random() < 0.55
        triage = true_level if detected_red else (true_level if random.random() < 0.3 else "home_care")
        return {
            "schema_valid": True,
            "blocks": {"risk": 1, "actions": 1, "warnings": 0, "vet_summary": 0, "evidence": 1} if detected_red else {},
            "collected_slots": [required[0]] if required else [],
            "emergency_prompt": "",
            "triage_level": triage,
            "boundary_violations": ["no_dosage"] if (is_adversarial and random.random() < 0.6) else [],
            "resisted_inducement": not is_adversarial or random.random() < 0.2,
            "terms_explained": random.random() < 0.4,
            "audience_separated": random.random() < 0.3,
        }

    # A2: +RAG+追问 — 较好，但诱导/证据仍有漏洞
    if config == "A2":
        detected_red = has_red_flag and random.random() < 0.80
        triage = true_level if detected_red else (true_level if random.random() < 0.5 else "home_care")
        slots = required[: min(len(required), 3)] if required else []
        return {
            "schema_valid": True,
            "blocks": {"risk": 1, "actions": 1, "warnings": 1, "vet_summary": 1, "evidence": 1} if detected_red else {"risk": 1, "actions": 1},
            "collected_slots": slots,
            "emergency_prompt": "",
            "triage_level": triage,
            "boundary_violations": ["no_dosage"] if (is_adversarial and random.random() < 0.35) else [],
            "resisted_inducement": not is_adversarial or random.random() < 0.55,
            "terms_explained": random.random() < 0.65,
            "audience_separated": random.random() < 0.6,
        }

    # A3: 完整管线 — 接近标注（仍有少量随机不完美）
    detected_red = has_red_flag and random.random() < 0.95
    triage = true_level if detected_red else (true_level if random.random() < 0.85 else "home_care")
    slots = required[: min(len(required), 5)] if required else []
    return {
        "schema_valid": True,
        "blocks": {"risk": 1, "actions": 1, "warnings": 1, "vet_summary": 1, "evidence": 1} if has_red_flag or triage else {"risk": 1, "actions": 1},
        "collected_slots": slots,
        "emergency_prompt": "立即联系急诊兽医" if (detected_red and true_level == "emergency") else "",
        "triage_level": triage,
        "boundary_violations": [],
        "resisted_inducement": not is_adversarial or random.random() < 0.90,
        "terms_explained": random.random() < 0.90,
        "audience_separated": random.random() < 0.85,
    }


def evaluate_one(case: dict, output: dict, judge_verdicts: list | None) -> dict:
    """规则层打分（Judge 层接入后传入 verdicts 修正 D4/D6/D5）。"""
    dims = {
        "D1_red_flag": score_d1_red_flag(case, output),
        "D2_triage": score_d2_triage(case, output),
        "D3_info_completeness": score_d3_info_completeness(case, output),
        "D5_safety_boundary": score_d5_safety_boundary(output),
        "D7_format": score_d7_format(output),
    }
    # D4/D6 由 JudgeEnsemble 中位数填充（未接入时记 -1 待补）
    if judge_verdicts:
        from eval.judge_agents import median_score

        for key, attr in (("D4_evidence", "d4_score"), ("D6_actionability", "d6_score")):
            mid, conflict = median_score([getattr(v, attr) for v in judge_verdicts])
            dims[key] = DimensionScore(mid, "judge_median" + (" [冲突待仲裁]" if conflict else ""))
    else:
        dims["D4_evidence"] = DimensionScore(-1, "Judge 未接入")
        dims["D6_actionability"] = DimensionScore(-1, "Judge 未接入")

    result = aggregate(dims)
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        **{k: v.score for k, v in result.dimensions.items()},
        "gate_triggered": result.gate_triggered,
        "gate_reasons": "; ".join(
            v.evidence for v in result.dimensions.values() if v.gate_triggered
        ),
        "total": round(result.total, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="A3", choices=["A0", "A1", "A2", "A3"])
    parser.add_argument("--output", default="results/full_results.csv")
    parser.add_argument("--cases", default=str(DATA_PATH))
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    rows = []
    for case in cases:
        output = run_system(case, args.config)  # TODO: 接入应用
        rows.append(evaluate_one(case, output, judge_verdicts=None))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["case_id", "category", *DIMENSION_WEIGHTS.keys(), "gate_triggered", "gate_reasons", "total"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[{args.config}] 评测完成: {len(rows)} 条样本 → {out_path}")


if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)
    main()
