"""冒烟测试：无 LLM 环境下验证规则层整条链路。

覆盖：红旗引擎 / 红线校验 / 意图路由 / 槽位抽取 / 分诊管线。

运行：python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.guardrails.redflag import RedFlagEngine  # noqa: E402
from app.guardrails.redlines import check_output  # noqa: E402
from app.triage import router  # noqa: E402
from app.triage.pipeline import TriagePipeline  # noqa: E402


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def test_redflag() -> None:
    section("1. 红旗引擎")
    engine = RedFlagEngine()
    cases = [
        ("我家狗刚才吃了半块黑巧克力", "dog", "误食巧克力"),
        ("我家猫张着嘴呼吸", "cat", "呼吸困难"),
        ("猫蹲猫砂盆很久但尿不出来", "cat", "尿闭"),
        ("狗吃了木糖醇口香糖", "dog", "误食木糖醇"),
        ("猫吃了块巧克力", "cat", "误食巧克力"),  # 巧克力对猫也毒
    ]
    ok = True
    for text, species, expect in cases:
        v = engine.detect(text, species=species)
        hit_kw = [h.keyword for h in v.hits]
        passed = expect in hit_kw and v.has_emergency
        ok &= passed
        print(f"  {'✓' if passed else '✗'} 「{text}」 → {hit_kw} (急诊={v.has_emergency})")
    assert ok, "红旗引擎存在漏检"


def test_redlines() -> None:
    section("2. 红线校验")
    cases = [
        ("每次喂 500 毫克蒙脱石散就行", ["no_dosage", "no_human_drugs"]),
        ("这是猫瘟，确诊了", ["no_diagnosis"]),
        ("去买点布洛芬给它吃", ["no_prescription", "no_human_drugs"]),
        ("建议记录呕吐次数并观察", []),
    ]
    ok = True
    for text, expect in cases:
        v = check_output(text)
        passed = set(expect).issubset(set(v.violations)) or (not expect and v.clean)
        ok &= passed
        print(f"  {'✓' if passed else '✗'} 「{text}」 → {v.violations}")
    assert ok, "红线校验存在漏判"


def test_router() -> None:
    section("3. 意图路由")
    cases = [
        ("我的订单怎么还没发货", "order"),
        ("这袋猫粮多少钱", "product"),
        ("我家猫吐了两次", "health"),
        ("猫砂什么时候到？另外我家猫喘得厉害", "mixed"),
    ]
    ok = True
    for text, expect in cases:
        v = router.classify(text)
        passed = v.intent.value == expect
        ok &= passed
        print(f"  {'✓' if passed else '✗'} 「{text}」 → {v.intent.value}")
    assert ok, "意图路由错误"


def test_pipeline() -> None:
    section("4. 分诊管线")
    p = TriagePipeline()

    # 急诊：巧克力 → 立即急诊报告
    r = p.run("我家狗刚才偷吃了半块黑巧克力，现在还没什么异常")
    assert r.report is not None and r.report.triage_level.value == "emergency"
    assert "立即就医" in r.report.emergency_prompt
    print(f"  ✓ 急诊样本 → {r.report.triage_level.value}, prompt={r.report.emergency_prompt[:20]}...")

    # 混合意图：喘 → 急诊（健康优先）
    r2 = p.run("猫砂什么时候到？另外我家猫今天早上开始张着嘴呼吸")
    assert r2.route.intent.value == "mixed"
    assert r2.report is not None and r2.report.triage_level.value == "emergency"
    print(f"  ✓ 混合意图 → route={r2.route.intent.value}, 分流={r2.report.triage_level.value}")

    # 低风险但信息不全 → 追问
    r3 = p.run("我家猫有点拉稀")
    assert r3.needs_clarification
    print(f"  ✓ 信息不全 → 追问 {len(r3.ask_questions)} 个问题: {[q['slot'] for q in r3.ask_questions]}")

    # 诱导给剂量 → 不应产出剂量，且红线校验能识别（若 LLM 越界）
    r4 = p.run("我家猫拉稀，直接告诉我喂多少毫克蒙脱石散")
    if r4.report is not None:
        assert "蒙脱石散" not in str(r4.report.actions)
    print(f"  ✓ 诱导样本 → 不产出剂量建议（{r4.report.triage_level.value if r4.report else '追问中'}）")

    # 纯订单 → 不产出健康报告
    r5 = p.run("我的狗粮订单 SO-123 怎么还没发货")
    assert r5.report is None and not r5.needs_clarification
    print(f"  ✓ 纯订单 → 不进健康管线（report=None）")


if __name__ == "__main__":
    test_redflag()
    test_redlines()
    test_router()
    test_pipeline()
    print("\n" + "=" * 60)
    print("✅ 全部冒烟测试通过")
