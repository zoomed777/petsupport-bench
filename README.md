# PetSupport-Bench

个人参赛作品｜犀牛鸟开源·混元大语言模型实战任务一｜非腾讯官方项目

## 我做的是什么

这是一个基于 Hy3 的宠物咨询 Agent，也包含一套评价它的回答质量的方法。我在之前的宠物商城客服项目上继续改造，没有训练或微调模型。

我关注的是一类容易被普通客服忽略的问题：用户在问订单、商品的时候，顺带提到了宠物身体不舒服。比如“换的猫粮什么时候到？我家猫最近食欲不好”。系统需要同时理解这两件事，发现已知风险时先回应，信息不够时继续问，再整理成主人能看懂的建议和就诊准备材料。

这类问题没有唯一标准答案，所以我除了做聊天应用，也做了七维评分标准、构造样本和评测脚本，检查回答质量以及评分方法本身的问题。健康内容只用于科普和就诊准备，不替代兽医诊断，不提供处方或药物剂量。

目前应用、样本、评测结果、报告和视频都已放在仓库。给导师看的整体说明在[项目介绍](docs/project_overview.md)，哪些文件上传了、哪些没有上传，写在[代码与文件说明](docs/submission_scope.md)。

## 可以先看这些

- [演示视频](demo/demo.mp4)：约 68 秒，展示聊天主流程。
- [实验报告](docs/experiment_report.md)：结果、失败案例和我的分析。
- [评估方法](docs/evaluation_protocol.md)：七个维度怎么打分。
- [完整评分表](results/final/results.csv) · [汇总数据](results/final/summary.json) · [逐条输出和调用记录](results/final/traces.jsonl)。
- [方法验证结果](results/final/validation_summary.json)：好中差排序、重复评分和对抗测试。
- [闸门触发记录](results/final/gate_audit.jsonl)：哪些是模型评审判断，哪些是规则命中。
- [记忆管理](docs/memory_management.md) · [四轮真实 Hy3 验证](results/memory/live_validation.json)。
- [录制参考脚本](docs/demo_script.md)。

## 应用怎么工作

当前入口是 `streamlit_demo.py`。用户直接在聊天框输入，不需要先选“健康”还是“订单”，也不用填写宠物档案表。系统会自动判断、追问，保留对话，并提供报告下载和每轮调用记录。具体交互见[聊天说明](docs/chat_demo.md)。

```text
用户消息 + 当前宠物档案和事件
  → 判断意图，检查已知紧急风险
  → 信息不足时追问
  → 匹配知识卡片，适用时调用 Hy3 整理文字
  → 检查输出，返回报告、追问或业务能力说明
```

有些回复由规则直接给出，不会调用 Hy3，页面会标明这一轮的实际情况。

现在还没接真实商城后台，所以不能查订单、物流、库存或直接转人工。知识库只有 8 条带来源的简短内容，用关键词匹配；旧客服工程中的向量检索和 Redis 模块虽然留在仓库里，但没有接入这条聊天流程。运行当前演示不需要 Java 商城、Redis 或业务数据库。

## 记忆做到哪一步了

- 按宠物保存档案和当前事件。明确说名字或另一只猫、狗时可以切换，归属不清楚时先确认。
- 年龄、体重、症状说错后可以更正，后续使用最新有效信息，并保留更正记录。
- 长历史整理成结构化事实摘要，同时限制近期原文、输入长度和请求预算，尚未排除的风险单独保留。
- 可以主动开启本机 SQLite 保存和恢复；新对话保留稳定档案，不沿用旧症状。
- 年龄、体重超过 30 天需要重新确认；单独问订单时不会带入旧健康事件。

这部分完成了 28 项聚焦测试和 4 轮真实 Hy3 验证。上下文预算用字节数保守估算，不是 Hy3 官方 tokenizer 的精确计数。本机数据库没有加密，也没做多用户登录隔离。视频录得较早，还没有展示后面补上的记忆功能。

## 如何运行

我验证使用的是 Python 3.11。在仓库目录打开 Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
Copy-Item .env.example .env
# 打开本地 .env，填写 HY3_BASE_URL、HY3_API_KEY、HY3_MODEL
.\.venv\Scripts\python.exe -m streamlit run streamlit_demo.py --server.address 127.0.0.1
```

浏览器访问 [本机演示页面](http://127.0.0.1:8501/)。依赖装好后也可以双击 `start_demo.cmd`。Linux/macOS 把解释器路径替换成 `.venv/bin/python`。

TokenHub 配置示例：`HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1`、`HY3_MODEL=hy3`。密钥只放在本机 `.env`，不要上传。默认使用在线模式；暂时没有可用 API 时，可以在侧栏“开发与演示设置”选择“离线规则”，先看交互流程。

如果旧 Anaconda 环境安装时遇到代理或 TLS 错误，可以换 Python 3.11 的独立环境，并尝试官方 HTTPS 源：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements-demo.txt
```

如果确认是本机代理配置引起的问题，而且网络允许直连，可在当前 PowerShell 临时设置 `$env:NO_PROXY='*'` 后再安装。不需要改用 HTTP 源或关闭证书校验。

## 如何复现实验

```powershell
.\.venv\Scripts\python.exe scripts/final_eval.py
.\.venv\Scripts\python.exe scripts/validate_final.py
.\.venv\Scripts\python.exe scripts/analyze_final.py
```

这批实验使用 `data/cases_final.jsonl` 中的 100 条构造样本，共 39 种不同表达，其中 25 条涉及混合意图、诱导或长文本干扰。样本不是实际病例，标签也没有经过兽医标注。

我比较了 A0（直接让 Hy3 回复）和 A3（规则、追问、知识卡片加 Hy3）。只评第一轮，两组同时改变了多个组件，因此这不是完整的多轮测试，也不能单独说明某一个组件的贡献。

评审使用同一 Hy3，评分时不提供配置名称。七个维度都要给出理由，药物红线另用规则检查。脚本保存请求 ID、模型、用量、原始输出、评分、错误和版本指纹，并支持续跑。在线重跑会消耗 API 额度；读取已有缓存不等于发起了新请求。

A0 均分为 83.70，A3 为 78.78。完整方案这次没有在总分上超过直接回复。我还发现评分规则会误判长否定句，所以闸门触发从 17 次到 4 次，不能直接解释成危险回答减少。分析和原始分歧都保留在报告里。

## 如何检查当前代码

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests/test_final_submission.py tests/test_memory.py -q
.\.venv\Scripts\python.exe scripts/smoke_test.py
.\.venv\Scripts\python.exe scripts/check_submission.py
```

这些测试针对本次参赛应用。仓库里的旧客服集成测试还需要原工程的额外依赖和服务。

## 数据和版本说明

批量评测对应提交 `6368460`。后面我修复了“食欲不好”等表达的识别问题，改成聊天界面，又补了记忆管理。这些改动有各自的测试，但没有重新跑完原来那套在线批量评测，因此这里的均分仍是旧实验版本的结果。相关记录见[修复说明](docs/bugfix_regression.md)和[记忆说明](docs/memory_management.md)。

`results/final/` 保存正式实验；汇总取当前版本指纹下各样本的最新记录，失败尝试也还在。`results/memory/` 是后来单独做的记忆验证，没有混进批量评分。

早期的 A0/A1/A2/A3 表、`full_results.csv` 和旧图是模拟数据。旧 `live_A3_results.csv` 缺少调用记录，评分也有问题，我没有采用它的结论。[8 月方案](docs/proposal.md)保留了最初的设计和后来调整的说明，当前功能以本文为准。

知识卡片在 `data/knowledge_base.jsonl`，附有 FDA、ASPCA、Cornell 和 Merck 的具体页面链接，覆盖范围还很小。模型调用通过 [Hy3](https://github.com/Tencent-Hunyuan/Hy3) 完成。

代码是在我之前的宠物商城客服项目上改造的。MIT 许可证适用于本仓库原创代码与构造样本；第三方资料的权利仍归原作者，引用来源不代表对方认可本项目。
