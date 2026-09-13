"""PetSupport-Bench recording-ready demo with persistent follow-up state."""
from __future__ import annotations
import json
from pathlib import Path
import streamlit as st
from app.triage.pipeline import TriagePipeline

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="PetSupport-Bench", page_icon="🐾", layout="wide")
st.title("🐾 PetSupport-Bench")
st.caption("宠物商城健康沟通与安全评测｜个人参赛作品，非腾讯官方项目")
st.info("仅用于健康科普与就诊准备，不替代兽医诊断，不提供药物剂量。")

with st.sidebar:
    st.header("宠物档案")
    mode = st.radio("运行方式", ["Hy3 在线", "离线规则"])
    species = st.selectbox("物种", ["未知", "猫", "狗"])
    age = st.selectbox("年龄段", ["未知", "幼年", "成年", "老年"])
    weight = st.number_input("体重（公斤；0 表示未知）", min_value=0.0, max_value=150.0, step=0.1)
    if st.button("清空会话"):
        for key in ["result", "message", "answers", "round"]:
            st.session_state.pop(key, None)
        st.rerun()
    st.caption("在线模式需要本地 .env。离线模式可演示规则，但不会调用 Hy3。")

profile = {"species":{"未知":None,"猫":"cat","狗":"dog"}[species],
           "age":{"未知":None,"幼年":"juvenile","成年":"adult","老年":"senior"}[age],
           "weight_kg":weight or None}
tab_app, tab_eval = st.tabs(["咨询演示", "评测结果"])
with tab_app:
    presets = [
        "狗粮怎么还没到？另外我家狗刚吃了半块黑巧克力，现在看着没事。",
        "我家猫有点拉稀。",
        "我的订单什么时候发货？",
    ]
    columns = st.columns(3)
    for col, label, value in zip(columns, ["混合意图＋紧急风险", "信息不足＋追问", "纯订单问题"], presets):
        if col.button(label):
            st.session_state["draft"] = value
    message = st.text_area("描述情况", key="draft", height=110)
    pipeline = TriagePipeline.from_environment() if mode == "Hy3 在线" else TriagePipeline()
    if mode == "Hy3 在线":
        if pipeline.client is None:
            st.warning("尚未配置 Hy3；请填写 .env，或选择离线规则演示。")
        else:
            st.caption("Hy3 配置已读取；是否调用成功以本次结果记录为准。")
    def run_current():
        flags = pipeline.engine.detect(st.session_state["message"], species=profile["species"])
        if flags.has_emergency:
            st.error(flags.emergency_prompt)
        with st.spinner("正在处理…"):
            result = pipeline.run(st.session_state["message"], profile=profile,
                                  answers=st.session_state["answers"], round_number=st.session_state["round"])
        st.session_state["result"] = result
    if st.button("运行 Agent", type="primary") and message.strip():
        st.session_state.update(message=message.strip(), answers={}, round=0)
        run_current()
    result = st.session_state.get("result")
    if result and result.ask_questions:
        if result.route and result.route.intent.value == "mixed":
            st.info("已识别到订单/商品咨询中包含健康描述，先补充宠物情况。订单部分未接入真实系统，需要由商城客服核实。")
        else:
            st.info("已识别到健康描述；信息还不够，先补充以下问题，再继续生成反馈。")
        st.subheader("请补充关键信息")
        st.caption("已填信息会保留；若出现呼吸困难、抽搐、不能排尿，请立即联系急诊兽医，不必等答完问题。")
        with st.form("followup"):
            values = {q["slot"]:st.text_input(q["question"], key="answer_"+q["slot"]) for q in result.ask_questions}
            submitted = st.form_submit_button("提交补充信息")
        if submitted:
            st.session_state["answers"].update({k:v.strip() for k,v in values.items() if v.strip()})
            st.session_state["round"] += 1
            run_current()
            st.rerun()
    result = st.session_state.get("result")
    if result:
        if result.report:
            st.markdown(result.report.render_markdown())
            st.download_button("下载就诊报告", result.report.render_markdown(), "pet-report.md", "text/markdown")
        elif not result.ask_questions:
            st.info("此页面未连接真实订单系统。请准备订单号或商品详情，联系商城人工客服核实；此处无法确认发货状态。")
        if result.llm_errors:
            st.warning("本轮 Hy3 请求失败，已使用规则模板。错误类型：" + ", ".join(result.llm_errors))
        elif result.llm_calls:
            st.success("本轮 Hy3 调用成功，共 " + str(len(result.llm_calls)) + " 次。")
        else:
            st.caption("本轮为规则输出/追问，没有调用 Hy3。")
        if result.blocked_fields:
            st.warning("模型字段触发红线，已保留模板：" + ", ".join(result.blocked_fields))
        with st.expander("查看可审计过程"):
            st.json({"intent":result.route.intent.value if result.route else None,
                     "collected_slots":result.collected_slots, "redflag":result.redflag,
                     "questions":result.ask_questions,"model_calls":result.llm_calls,
                     "errors":result.llm_errors,"blocked_fields":result.blocked_fields})
with tab_eval:
    path = ROOT / "results/final/summary.json"
    if path.exists():
        summary = json.loads(path.read_text(encoding="utf-8"))
        st.subheader("真实 A0/A3 对比")
        st.caption("冻结实验版本：6368460。后续路由修复仅做回归验证，此处不是修复版重新评测的成绩。")
        st.caption("构造场景、单模型自动评审；没有人工/兽医标注，不代表临床安全认证。")
        for col, (name, info) in zip(st.columns(2), summary["configurations"].items()):
            col.metric(name + " 平均分", info["mean"])
            col.write(f'完成评分 {info["scored"]}；闸门触发 {info["gate_count"]}；生成调用 {info["generation_calls"]}')
        csv_path = ROOT / "results/final/results.csv"
        if csv_path.exists():
            st.download_button("下载完整评分表", csv_path.read_bytes(), "results.csv", "text/csv")
        st.json(summary)
    else:
        st.info("正式评测仍在运行。完成后这里显示真实结果。")
