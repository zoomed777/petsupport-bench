# PetSupport-Bench

> 个人参赛作品｜犀牛鸟开源·混元大语言模型实战任务一｜非腾讯官方项目
>
> 宠物商城健康沟通与可信评测原型。健康输出仅为科普与就诊准备，不替代兽医诊断，不提供处方或剂量。

## 评委阅读入口

- [最终实验报告](docs/experiment_report.md)：真实结果、典型失败、限制
- [七维评估方法](docs/evaluation_protocol.md)
- [最终结果汇总](results/final/summary.json) · [完整评分表](results/final/results.csv)
- [逐条输出与调用记录](results/final/traces.jsonl)
- [判别力、重复性与攻击实验](results/final/validation_summary.json)
- [闸门来源审计](results/final/gate_audit.jsonl)：区分语义评审和规则命中；次数不等于实际危险回答数
- [两分钟录制脚本](docs/demo_script.md)（脚本不是视频；视频由参赛者另附）

## 场景与实现

用户在订单或商品咨询中夹带“误食”“不能排尿”等描述时，系统优先响应风险，收集缺失信息，并提供就诊准备材料。开放式回复的质量取决于安全性、证据和沟通，而非匹配唯一标准答案。

当前演示入口是 `streamlit_demo.py`：宠物档案、三个预置案例、持续追问表单、报告下载、实际调用状态、评测结果页。无需 Java 商城、Redis、向量模型或真实业务数据库。

```text
消息 + 宠物档案
  → 意图规则（低置信时可调用 Hy3）
  → 红旗检测 → 紧急提示 / 关键信息追问
  → 知识卡片 + Hy3 文案增强（仅适用路径调用）
  → 字段红线检查 → 报告 / 追问 / 业务能力说明
```

纯订单分支只说明未接入业务系统，没有实际查单或转人工。知识检索是小型知识库关键词匹配；原始客服/RAG代码仍保留用于溯源，但不属于本次Demo运行路径。规则是有限防线，不能保证全部危险输出都被检出。

## 运行（Python 3.11）

Windows PowerShell，在仓库目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
Copy-Item .env.example .env
# 编辑本地 .env，填入 HY3_BASE_URL、HY3_API_KEY、HY3_MODEL
.\.venv\Scripts\python.exe -m streamlit run streamlit_demo.py --server.address 127.0.0.1
```

已安装依赖的本机可双击 `start_demo.cmd`。浏览器访问 http://127.0.0.1:8501 。Linux/macOS 使用 `.venv/bin/python` 替换解释器路径。

TokenHub 示例：`HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1`、`HY3_MODEL=hy3`；密钥仅保存在本地 `.env`。API不可用时选择“离线规则”，页面会明确说明未调用模型。

如果旧Anaconda环境安装时报代理/TLS错误，使用Python 3.11的独立环境。可在当前PowerShell临时设置 `$env:NO_PROXY='*'` 后通过官方HTTPS源安装：
` .\.venv\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements-demo.txt `。
无需使用HTTP源或关闭证书校验。

## 复现实验

```powershell
.\.venv\Scripts\python.exe scripts/final_eval.py
.\.venv\Scripts\python.exe scripts/validate_final.py
.\.venv\Scripts\python.exe scripts/analyze_final.py
```

- 固定数据：`data/cases_final.jsonl`，100条作者构造样本、39种不同表达，包含25条混合/诱导/长文本难例。不是临床病例集，标签不是兽医金标准。
- A0：直接Hy3回复；A3：规则、追问、知识卡片与Hy3增强。仅评第一轮，不能称为多轮或单组件消融。
- 七维Judge对最终文字盲评（不提供配置名），药物红线另用规则核验。所有维度必须有具体理由。
- 记录请求ID、模型、用量、原始输出、失败、评分和输入/代码哈希。支持续跑；失败不会伪装成成功。
- 判别力：4种情境的好/中/差输出；一致性：同输出3次评审；对抗：伪引用和评分指令注入。
- 同一Hy3生成/评审存在共同偏差；重复性不等于人工一致性。最终报告如实保留低分和失败。
- 实验发现长否定句的规则误报，原评分保持冻结并公开审计；不能用闸门触发差异宣称实际安全风险下降。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests/test_final_submission.py -q
.\.venv\Scripts\python.exe scripts/smoke_test.py
.\.venv\Scripts\python.exe scripts/check_submission.py
```

仅上述测试覆盖最终参赛路径。旧客服系统的集成测试需要其原有额外依赖和服务。

## 数据与版本说明

`docs/proposal.md` 是8月方案，包含当时计划中的功能，完成范围以本文与最终报告为准。
旧 `results/A*_results.csv`、`full_results.csv` 及旧图是模拟结果。
旧 `live_A3_results.csv` 没有调用审计且评分有问题，均不能作为最终实验结论。
正式结果只读取 `results/final/`；该目录保留失败尝试，汇总以当前哈希下最新记录为准。

知识库8条简短转述附FDA、ASPCA、Cornell和Merck具体页面链接，见 `data/knowledge_base.jsonl`。只做有限来源核对，不声称临床认证。模型调用使用[Hy3](https://github.com/Tencent-Hunyuan/Hy3)，没有训练或微调。

代码基于作者此前宠物商城客服项目改造。MIT许可证适用于本仓库原创代码与构造样本；第三方资料权利归原作者，链接与短摘要不代表其为本项目背书。
