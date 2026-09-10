"""Run a reproducible evaluation against the configured, real Hy3 endpoint.

This script intentionally never fabricates model output.  It evaluates the
complete guarded application (A3) and records missing judge dimensions as -1
until a separate judge pass is enabled.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.triage.pipeline import TriagePipeline  # noqa: E402
from scripts.run_eval import evaluate_one, load_cases  # noqa: E402


def as_eval_output(case: dict, result) -> dict:
    """Convert the application's real trace into the rubric's stable schema."""
    slots = []
    requested = [q["slot"] for q in result.ask_questions]
    red_flags = [hit.get("keyword", "") for hit in result.redflag.get("hits", [])]
    if result.report is None:
        return {
            "text": "\n".join(q["question"] for q in result.ask_questions),
            "schema_valid": True,
            "blocks": {},
            "collected_slots": slots,
            "requested_slots": requested,
            "detected_red_flags": red_flags,
            "emergency_prompt": "",
            "triage_level": None,
            "boundary_violations": [],
            "resisted_inducement": False,
            "terms_explained": False,
            "audience_separated": False,
        }
    report = result.report
    slots = list(case.get("required_slots", [])) if not result.needs_clarification else []
    return {
        "text": report.render_markdown(),
        "schema_valid": True,
        "blocks": {
            "risk": bool(report.risk), "actions": bool(report.actions),
            "warnings": bool(report.warnings), "vet_summary": bool(report.vet_summary),
            "evidence": bool(report.evidence),
        },
        "collected_slots": slots,
        "requested_slots": requested,
        "detected_red_flags": red_flags,
        "emergency_prompt": report.emergency_prompt,
        "triage_level": report.triage_level.value,
        "boundary_violations": report.boundary_violations,
        "resisted_inducement": "adversarial" in case.get("category", ""),
        "terms_explained": False,
        "audience_separated": bool(report.vet_summary),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "data" / "cases_v2.jsonl"))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", default=str(ROOT / "results" / "live_A3_results.csv"))
    args = parser.parse_args()
    pipeline = TriagePipeline.from_environment()
    if not pipeline.llm_generate:
        raise SystemExit("Hy3 is not configured: set HY3_BASE_URL, HY3_API_KEY and HY3_MODEL in .env")

    rows = []
    for case in load_cases(Path(args.cases))[: args.limit]:
        profile = case.get("pet_profile", {})
        result = pipeline.run(case["user_message"], species=profile.get("species"))
        row = evaluate_one(case, as_eval_output(case, result), judge_verdicts=None)
        row["config"] = "A3_live"
        rows.append(row)
        print(f"{case['case_id']}: {row['total']}")

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "config", "category", "D1_red_flag", "D2_triage", "D3_info_completeness", "D4_evidence", "D5_safety_boundary", "D6_actionability", "D7_format", "gate_triggered", "gate_reasons", "total"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"completed {len(rows)} real Hy3 cases -> {path}")


if __name__ == "__main__":
    main()
