"""PetSupport-Bench Streamlit 演示界面。

功能：
- 配置宠物档案
- 输入问题（支持混合意图：订单/商品/健康）
- 展示意图路由、红旗检测、槽位抽取、追问、五区块照护报告
- 显示完整决策链路（可解释性）

运行：streamlit run streamlit_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.triage_schemas import TriageLevel  # noqa: E402
from app.triage.pipeline import TriagePipeline  # noqa: E402

st.set_page_config(page_title="PetSupport-Bench 演示", layout="wide")

st.title("🐾 PetSupport-Bench · 宠物商城 AI 客服")
st.caption("个人参赛作品｜犀牛鸟开源·混元大语言模型实战任务｜非腾讯官方项目")


def severity_color(level: TriageLevel | None) -> str:
    if level is None:
        return "gray"
    return {
        TriageLevel.home_care: "green",
        TriageLevel.routine: "blue",
        TriageLevel.within_24h: "orange",
        TriageLevel.emergency: "red",
    }.get(level, "gray")


# ---------------- 侧边栏：宠物档案 ----------------
with st.sidebar:
    st.header("宠物档案")
    species = st.selectbox("物种", ["未知", "猫", "狗"], index=0)
    age = st.selectbox(
        "年龄段",
        ["未知", "幼年（0-12 月）", "成年（1-7 岁）", "老年（7 岁以上）"],
        index=0,
    )
    weight = st.text_input("体重（公斤，可选）", "")
    st.divider()
    st.markdown(
        "**安全声明**：本演示输出为健康科普与就医准备参考，"
        "**不替代执业兽医诊断**。"
    )

species_map = {"未知": None, "猫": "cat", "狗": "dog"}
age_map = {
    "未知": None,
    "幼年（0-12 月）": "juvenile",
    "成年（1-7 岁）": "adult",
    "老年（7 岁以上）": "senior",
}

pipeline = TriagePipeline()

# ---------------- 主界面：输入 ----------------
st.header("向客服提问")
st.markdown("示例：`狗粮怎么还没到？另外我家狗刚吃了半块黑巧克力`")

user_message = st.text_area("输入消息", height=80, value="")

if st.button("运行 Agent", type="primary") and user_message.strip():
    # 决策链路展开
    with st.expander("🔍 完整决策链路", expanded=True):
        result = pipeline.run(user_message, species=species_map[species])

        # 意图路由
        st.subheader("1. 意图路由")
        if result.route is None:
            st.error("路由失败")
        else:
            intent_name = result.route.intent.value
            st.write(
                f"判定意图：`{intent_name}` "
                f"（置信度 {result.route.confidence:.2f}）"
            )
            st.json(result.route.matched)
            if not result.route.needs_health_pipeline:
                st.info("非健康类问题，交给原 ReAct 工具链处理（订单/商品查询）。")
                st.stop()

        # 红旗检测
        st.subheader("2. 红旗风险检测")
        if result.redflag:
            st.json(result.redflag)
            if result.redflag.get("has_emergency"):
                st.error("⚠️ 命中急诊红旗")
            elif result.redflag.get("max_level"):
                st.warning(f"⚠️ 命中风险等级：{result.redflag['max_level']}")
            else:
                st.success("未命中红旗")
        else:
            st.success("未命中红旗")

        # 槽位 / 追问
        st.subheader("3. 槽位与追问")
        if result.needs_clarification:
            st.warning("关键信息缺失，生成追问：")
            for q in result.ask_questions:
                st.markdown(f"- **{q['slot']}**：{q['question']}")
        else:
            st.success("关键槽位已足够")

    # 报告输出
    if result.report is not None:
        st.divider()
        report = result.report
        col1, col2 = st.columns([1, 3])
        with col1:
            st.metric(
                "分流等级",
                report.triage_level.value,
                help="home_care=居家观察 / routine=常规预约 / within_24h=24h内就诊 / emergency=立即急诊",
            )
        with col2:
            if report.emergency_prompt:
                st.error(report.emergency_prompt)

        st.markdown(report.render_markdown())

        with st.expander("📦 结构化输出（JSON）"):
            st.json(report.model_dump())

        if report.boundary_violations:
            st.error(f"🚨 触发安全红线：{report.boundary_violations}")
        elif report.triage_level.value == "emergency":
            st.success("✅ 急诊场景已正确强制就医提示")

st.divider()
st.caption("代码仓库：PetSupport-Bench（基于 PetHub 客服系统改造，模型切换为 Hy3）")
