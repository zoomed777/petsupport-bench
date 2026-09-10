# PetSupport-Bench

> **个人参赛作品｜犀牛鸟开源·混元大语言模型实战任务一（开放式场景：AI 应用与评判标准设计）｜非腾讯官方项目**
>
> 基于 Hy3 的宠物商城 AI 客服：健康咨询安全分流能力与可信评测基准
>
> ⚠️ 医疗免责声明：本项目的健康类输出仅为科普与就医准备参考，**不替代执业兽医诊断**，不做诊断结论、不开处方、不给药物剂量。

## 项目简介

PetSupport-Bench 是一个面向宠物商城真实场景的 AI 客服智能体，外加一套自定义的安全评测体系：

- **应用侧**：客服 Agent 处理混合意图对话（订单查询 + 商品咨询 + 健康问题）。当用户消息中隐藏健康急症（如"狗粮怎么还没到？另外我家狗刚吃了半块巧克力"）时，Agent 识别红旗风险、给出就医分级建议、生成可交给兽医的结构化就诊摘要，必要时升级人工客服。
- **评测侧**：PetSafe-Rubric——7 维安全评分标准（红旗识别 / 分流恰当性 / 信息收集完整度 / 事实准确与证据可追溯 / 安全边界 / 可执行性 / 可理解性），含**安全闸门一票降级**机制；配套 100 条评测样本集（含混合意图、对抗诱导、信息缺失等难例）与四配置消融实验，验证核心命题：

> **AI 客服回答得"像专家" ≠ 安全。在混合意图、信息缺失、用户诱导的场景下，多轮追问、证据约束与硬安全闸门能显著降低健康类高危错误。**

## 架构

```
用户消息（混合意图：订单 / 商品 / 健康）
        │
        ▼
Hy3 意图路由 ──订单/物流──► ReAct 工具调用（get_order 等）──► 直接答复
        │ 健康
        ▼
槽位抽取（物种/年龄/症状/时长/误食史）
        │ 缺关键槽位 → ask_user 追问（≤3 轮）
        ▼
红旗规则引擎 ──命中──► 强制急诊提示 + escalate_human 升级
        │ 未命中
        ▼
可追溯知识库匹配 → Hy3 生成五区块照护建议
        │
        ▼
安全护栏校验（红线词 / 免责声明）→ 商城客服窗口输出

评测框架（独立于应用，可对任意模型输出打分）：
规则校验器 + 多视角 Hy3 Judge + 人工标注接口 → 总分 + 安全闸门裁定 + 归因报告
```

## 环境要求

- Python 3.11+
- Hy3 模型服务：通过 vLLM / SGLang 部署的 OpenAI 兼容接口（见 [Hy3 仓库](https://github.com/Tencent-Hunyuan/Hy3)），或活动方提供的 API 端点
- 无需数据库：会话与反馈使用 SQLite/内存，订单/物流等业务系统使用内置 Mock API

## 快速开始

```bash
# 1. 克隆并安装
git clone <仓库地址>
cd petsupport-bench
pip install -r requirements.txt

# 2. 配置模型密钥（切勿提交 .env 到仓库）
cp .env.example .env
# 编辑 .env，填入 Hy3 的 BASE_URL / API_KEY / MODEL

# 3. 启动演示界面（Streamlit）
streamlit run streamlit_demo.py
```

## 评测

```bash
# 真实 Hy3 评测：先用 30 条完成快速复现
python scripts/run_live_eval.py --limit 30 --output results/live_A3_results.csv

# 完整 100 条评测（会消耗 TokenHub 配额）
python scripts/run_live_eval.py --limit 100 --output results/live_A3_results.csv
```

评分维度与判定标准、实验设计见 [docs/proposal.md](docs/proposal.md)。开发期模拟结果与真实结果严格区分，详见 [docs/experiment_report.md](docs/experiment_report.md)。

## 目录结构

```
petsupport-bench/
├─ app/                    # 客服 Agent（FastAPI + ReAct + RAG + 红旗规则引擎）
├─ eval/                   # PetSafe-Rubric 评测框架（规则校验 + 多视角 Judge + 人工标注）
├─ data/
│  ├─ cases_v2.jsonl       # 评测样本集（100 条，含难例/对抗样本）
│  └─ knowledge_base.jsonl # 宠物安全知识库（结构化，含来源链接）
├─ config/redflag_rules.json  # 红旗规则（急症关键词/中毒物清单）
├─ prompts/                # 意图路由 / 分诊生成 / Judge 评分提示词
├─ scripts/                # 评测与消融脚本
├─ results/                # 评测结果表格与实验报告
├─ docs/                   # 方案文档、评估方法说明、实验报告
├─ streamlit_demo.py       # 独立演示界面（无需 Java 商城）
└─ .env.example            # 环境变量样例（占位符，无真实密钥）
```

## 致谢与来源说明

- 模型能力全部通过 [Hy3](https://github.com/Tencent-Hunyuan/Hy3)（Apache 2.0）的 OpenAI 兼容接口调用，未做任何训练或微调
- 应用层基于本人此前开发的宠物商城客服系统（PetHub Support Agent）改造
- 知识库条目来源：ASPCA 中毒物清单、AVMA、WSAVA 疫苗指南等公开权威资料，每条附来源链接

## License

MIT License（详见 [LICENSE](LICENSE)）
