"""Chat-first PetSupport demo with automatic intent routing and follow-up."""
from __future__ import annotations
import json
from pathlib import Path
import streamlit as st
from app.chat_session import ChatSession
from app.triage.pipeline import TriagePipeline

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="PetSupport · 宠物咨询", page_icon="🐾", layout="centered", initial_sidebar_state="collapsed")
st.title("🐾 PetSupport")
st.caption("说说你和宠物遇到的事，我会先了解情况，再帮你梳理下一步。")
st.caption("个人参赛作品 · 基于 Hy3 · 非腾讯官方项目｜健康科普与就诊准备，不替代兽医诊断。")

with st.sidebar:
    st.header("会话设置")
    if st.button("开始新对话"):
        st.session_state.pop("chat_session", None)
        st.session_state.pop("chat_messages", None)
        st.rerun()
    st.caption("更换宠物或开始不相关的话题时，请新建对话，避免混用先前信息。")
    with st.expander("开发与演示设置"):
        mode = st.radio("模型连接", ["Hy3 在线", "离线规则"])
        st.caption("默认在线。离线模式只运行规则，不调用 Hy3。密钥从本地环境读取。")

st.session_state.setdefault("chat_session", ChatSession())
st.session_state.setdefault("chat_messages", [])
session = st.session_state["chat_session"]
messages = st.session_state["chat_messages"]

tab_app, tab_eval = st.tabs(["和我聊聊", "评测与项目"])
with tab_app:
    if not messages:
        with st.chat_message("assistant", avatar="🐾"):
            st.markdown("你好！你可以直接描述情况，不用选择问题类型。\n\n比如：**“换的猫粮什么时候到？我家猫最近食欲不好。”**\n\n我会自动判断需要先关注什么，信息不够时再向你追问。")
    for index, message in enumerate(messages):
        with st.chat_message(message["role"], avatar="🐾" if message["role"] == "assistant" else None):
            st.markdown(message["content"])
            audit = message.get("audit")
            if audit:
                if audit["errors"]:
                    st.warning("本轮部分模型请求失败，已使用规则信息继续处理。")
                elif audit["model_calls"]:
                    st.caption(f'本轮 Hy3 调用成功 · {len(audit["model_calls"])} 次（含信息提取或生成）')
                else:
                    st.caption("本轮为规则识别与回复，未调用 Hy3。")
                with st.expander("本轮判断依据与调用记录"):
                    st.json(audit)
            if message.get("report"):
                st.download_button("保存这份就诊准备报告", message["report"], "pet-report.md", "text/markdown", key=f"report_{index}")

    text = st.chat_input("直接说说情况，或回答我刚才的问题…", max_chars=4000)
    if text and text.strip():
        messages.append({"role": "user", "content": text.strip()})
        with st.chat_message("user"):
            st.markdown(text)
        pipeline = TriagePipeline.from_environment() if mode == "Hy3 在线" else TriagePipeline()
        with st.chat_message("assistant", avatar="🐾"):
            if mode == "Hy3 在线" and pipeline.client is None:
                st.warning("未配置 Hy3，当前仅用规则回复。")
            flags = pipeline.engine.detect(session.context_with(text.strip()))
            if flags.has_emergency:
                st.error(flags.emergency_prompt)
            with st.spinner("正在了解你的情况…"):
                reply = session.reply(text, pipeline)
        messages.append({"role": "assistant", **reply})
        st.rerun()

with tab_eval:
    st.subheader("PetSupport-Bench · 可审计评测")
    st.caption("100条作者构造样本、39种表达；不是临床病例或兽医标注。")
    path = ROOT / "results/final/summary.json"
    if path.exists():
        summary = json.loads(path.read_text(encoding="utf-8"))
        st.caption("冻结实验版本：6368460。后续路由和聊天交互修复单独验证，以下不是新版重新评测的成绩。")
        for col, (name, info) in zip(st.columns(2), summary["configurations"].items()):
            col.metric(name + " 平均分", info["mean"])
            col.write(f'完成评分 {info["scored"]}；闸门触发 {info["gate_count"]}')
        st.warning("规则存在否定句误报；闸门次数不等于真实危险回答数量，不能据此宣称安全风险下降。")
        csv_path = ROOT / "results/final/results.csv"
        if csv_path.exists():
            st.download_button("下载完整评分表", csv_path.read_bytes(), "results.csv", "text/csv")
        validation_path = ROOT / "results/final/validation_summary.json"
        if validation_path.exists():
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            st.write(f'评估方法验证：{validation["completed"]}次评审；好/中/差严格排序 {validation["strict_good_medium_bad"]}/{validation["ranked_scenarios"]}；重复评分平均标准差 {validation["mean_repeat_sd"]}。')
            st.caption("同模型自动评审；重复性不等于人工一致性。")
        with st.expander("实验配置"):
            st.json(summary)
        st.markdown("[查看开源代码与完整报告](https://github.com/zoomed777/petsupport-bench)")
    else:
        st.info("尚未找到正式评测结果。")
