# 代码与文件说明

更新日期：2026-09-14。记忆初版为 `dcb9aab`，之后整理了文档，本次又补了多轮测试、两处记忆修复和验证缓存保护。新增实验各自记录代码指纹，见[多轮报告](multiturn_report.md)。

## 一、代码有没有漏交

本次聊天应用需要的项目源码已经提交到 `petsupport-bench`。没有另外留着一份未公开的核心实现。运行时需要自己配置 Hy3 密钥、安装依赖，但不需要我电脑里的其他工程或私人数据库。

这里有三种情况容易混在一起，我分开说明：

- 密钥、依赖安装目录和本机聊天数据没有上传。
- 旧客服代码有一部分留在仓库里，但没有接入现在的聊天入口。
- 真实商城集成、云端多用户记忆等功能还没做，不是做完了但没交代码。

## 二、仓库里有哪些材料

| 材料 | 位置 | 内容 |
|---|---|---|
| 应用入口 | `streamlit_demo.py`、`start_demo.cmd` | 当前聊天演示和启动脚本 |
| 核心源码 | `app/chat_session.py`、`app/memory.py`、`app/memory_store.py`、`app/triage/`、`app/guardrails/`及相关模型和配置 | 聊天、记忆、信息处理和输出检查 |
| 环境配置 | `requirements-demo.txt`、`.env.example`、README | 依赖版本、空白配置和运行方法 |
| 数据与评分器 | `data/cases_final.jsonl`、`data/knowledge_base.jsonl`、`prompts/final_judge.md`、`eval/final_judge.py`及相关脚本 | 构造样本、知识卡片、七维评分 |
| 实验记录 | `results/final/`、`results/memory/`、`results/multiturn_v1_*/` | 原始输出、调用记录、评分、初版记忆与新增多轮验证 |
| 测试 | `tests/test_final_submission.py`、`tests/test_memory.py`、`tests/test_eval_artifacts.py`、`.github/workflows/submission.yml` | 当前 62 项测试和 GitHub 自动检查 |
| 文档与视频 | `docs/`、`demo/demo.mp4` | 设计、报告、使用说明和录制视频 |

## 三、哪些文件没有上传，为什么

| 文件 | 原因 | 换一台电脑怎么运行 |
|---|---|---|
| `.env`、密钥文件 | 含 API 访问凭据，不能公开 | 复制 `.env.example`，填写自己的 Hy3 配置 |
| `.venv/`、`venv/`、依赖缓存 | 是本机安装的第三方环境 | 用 Python 3.11 按 `requirements-demo.txt` 安装 |
| `.local/`、数据库文件 | 可能含宠物档案和聊天隐私 | 程序会创建空库，使用自己的测试信息即可 |
| `__pycache__/`、测试缓存、日志和构建产物 | 运行时会生成，部分还可能含用户输入 | 启动程序或运行测试后会重新生成 |

这些规则写在[.gitignore](../.gitignore)里。公开的评测记录不包含 API 密钥。线上模型调用需要有效的 API 服务；这部分配置由运行者自行提供。

## 四、旧客服代码和这次项目的关系

这次是在我原来的客服工程上改造，不是所有内容都重新从零写。仓库保留了一些旧模块，方便查看来源，但当前入口只使用其中一部分。

| 旧代码 | 现在的情况 |
|---|---|
| `app/agents/ticket_agent.py`、`app/agents/state.py`、`app/agents/tools.py` | 有旧 Agent 和 Redis 会话实现；现在的记忆走 `app/memory.py` 和 SQLite |
| `app/services/` 中的向量检索、重排和旧回复生成 | 当前没有接这条链路，知识卡片用关键词匹配 |
| `app/main.py`、`app/api/`、`app/static/` | 属于旧 API 和页面，不是 README 中的 Streamlit 入口 |
| `pyproject.toml` 中的旧依赖组合 | 仍保留；运行当前演示按 `requirements-demo.txt` 安装即可 |

本机旁边的 `zoomed` 和 `customer-support-agent-main` 没有作为完整工程一起提交。部分旧代码已经迁到本仓库，当前应用不依赖这些旁边的目录。这次的文件检查针对参赛仓库，没有逐一检查所有旧工程。

## 五、历史方案和结果怎么看

[最初方案](proposal.md)里有四配置消融、较大知识库、人工标注等计划，最后并没有全部完成。我在文档里补了实际调整，当前实现以 README 和项目介绍为准。

旧模拟结果、旧图，以及评分有问题的 `live_A3_results.csv` 都保留作开发记录，没有用来支持最终结论。

批量评测、聊天修复、记忆升级和视频分别对应不同时间的版本。后来新增功能没有重新跑完整批量评测，我把各自的验证记录分开保存了。还没完成的部分见[项目介绍](project_overview.md)第六节。
