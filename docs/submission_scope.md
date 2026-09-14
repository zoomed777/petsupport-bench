# 交付范围与未提交内容说明

核对日期：2026-09-14。功能基线：`dcb9aabd1b1e1f35e97f930153b1a96606d11e7d`。本次导师说明是该功能版本之后的文档补充。

## 1. 是否有漏交代码？

核对时，`petsupport-bench`工作区无待提交文件，本地HEAD与GitHub的main均为上述提交；未发现排除虚拟环境、依赖和缓存目录之外，被忽略的额外源码/说明文件。**当前参赛应用的实现代码没有待提交或待推送部分。** 此结论仅针对该仓库，不代表整台电脑或所有相关旧工程均已公开。

必须区分：

- **未提交的运行文件**：密钥、安装依赖、本机数据等，出于安全或可复现惯例不上传。
- **已提交但未接入的代码**：仓库中保留的旧客服系统模块，不属于当前演示运行路径。
- **尚未实现的能力**：例如真实商城集成、云端多用户记忆，不能说成“代码没有提交”。

## 2. 已提交并用于当前交付的材料

| 类别 | 路径 | 说明 |
|---|---|---|
| 应用入口 | `streamlit_demo.py`、`start_demo.cmd` | 当前演示入口，不依赖旁边的旧工程目录 |
| 核心源码 | `app/chat_session.py`、`app/memory.py`、`app/memory_store.py`、`app/triage/`、`app/guardrails/`及相关模型/配置 | 聊天、记忆、处理管线与输出检查 |
| 环境说明 | `requirements-demo.txt`、`.env.example`、README | 提供依赖声明和空白配置，不提供密钥 |
| 数据和评测 | `data/cases_final.jsonl`、`data/knowledge_base.jsonl`、`prompts/final_judge.md`、`eval/final_judge.py`、相关脚本 | 作者构造样本、有限知识卡片、评判规则 |
| 结果和验证 | `results/final/`、`results/memory/` | 原始输出、调用记录、评分及独立记忆验证 |
| 测试和自动检查 | `tests/test_final_submission.py`、`tests/test_memory.py`、`.github/workflows/submission.yml` | 聚焦测试、GitHub自动检查；不表示旧系统全部测试都已验收 |
| 说明与演示 | `docs/`、`demo/demo.mp4` | 报告、方法、功能边界及真实录制视频 |

## 3. 不随仓库上传的文件及原因

| 文件/目录 | 不上传原因 | 导师如何获得等效运行条件 |
|---|---|---|
| `.env`、密钥文件 | 包含访问凭据；不能公开 | 复制`.env.example`，填写自己的Hy3接口和密钥 |
| `.venv/`、`venv/`、依赖缓存 | 是本机安装产物，不是本项目遗漏源码 | 按`requirements-demo.txt`重新安装；当前验证环境为Python 3.11 |
| `.local/`、数据库文件 | 开启保存后产生的个人宠物档案与聊天记录，可能含隐私 | 空库可自动创建，使用自己的测试信息；不需要作者的私人数据库 |
| `__pycache__/`、测试缓存、日志、构建产物 | 可重新生成，部分日志可能含用户输入 | 运行程序或测试时生成 |

上述排除规则见[.gitignore](../.gitignore)。未上传作者的API额度或本机环境，不影响源码交付的完整性；但在线模型复现需要有效API服务。公开的评测记录不包含API密钥。

## 4. 已在仓库中、但没有接入当前演示的旧模块

| 旧代码 | 当前关系 |
|---|---|
| `app/agents/ticket_agent.py`、`app/agents/state.py`、`app/agents/tools.py` | 保留旧客服Agent及Redis会话相关实现；当前聊天记忆走`app/memory.py`与SQLite，不经过该Redis层 |
| `app/services/`中的向量检索、重排和旧回复生成等模块 | 保留供工程溯源；当前知识检索是小型知识卡片关键词匹配，不宣称已启用旧向量/RAG链路 |
| `app/main.py`、`app/api/`、`app/static/`等旧API/页面 | 不是README指定的当前Streamlit入口；旧工程依赖与服务需另行配置 |
| `pyproject.toml`中的旧客服依赖组合 | 保留兼容性元数据；当前演示按`requirements-demo.txt`安装，不要求运行全部旧集成 |

工作区另有`zoomed`、`customer-support-agent-main`等相关目录，它们不是本次仓库的整体交付对象。部分相关代码已经迁入当前仓库，因此不能将“旧工程未整体提交”表述为“所有旧工程代码都未提交”。本次未对这些旁系目录进行逐文件完整性审计，导师运行当前应用也不需要它们。

## 5. 历史材料不能当作当前完成证明

- `docs/proposal.md`为历史方案，其中的四配置消融、生产部署等计划不等于已完成；README与最终报告优先。
- 旧A0/A1/A2/A3模拟结果、旧图和有评分问题的`live_A3_results.csv`保留供溯源，不作为真实实验结论。
- 冻结单轮评测、后续聊天修复、记忆v2和视频分别对应不同版本，不能将它们拼接成“最新版本已完成同一套全面验证”。

尚未实现或尚未验证的能力，请看[导师项目说明第六节](project_overview.md#六完成状态与尚未覆盖的部分)。后续若继续开发，应新增版本与验证记录，不覆盖原始结果。
