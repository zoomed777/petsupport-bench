# 迁移清单：从 customer-support-agent-main 迁移到 petsupport-bench

> 前提：`customer-support-agent-main` 目前**不是 git 仓库**（已确认），不存在历史密钥泄漏风险。
> 参赛仓库全新 `git init`，只复制下列白名单内容，`.env` 一律不复制。

## 一、复制（白名单）

| 来源 | 去向 | 说明 |
|---|---|---|
| `app/`（agents / api / core / services / models） | `app/` | ReAct 循环、ask_user、escalate_human、RAG 管线、mock 业务 API |
| `app/knowledge/pet_care.md` | 归档参考 | 内容已并入 `data/knowledge_base.jsonl`（结构化+来源），后续以 JSONL 为准 |
| `prompts/`（react_v1/v2 等） | `prompts/` | 保留 A/B prompt 基础，新增意图路由与分诊 prompt |
| `tests/` 中与 Agent 核心相关的用例 | `tests/` | 确认无真实数据后保留 |
| `pyproject.toml`（依赖清单） | 根目录 | 检查依赖最小化 |

## 二、删除（不进参赛仓库）

- [ ] `venv/`、`.pytest_cache/`、`*.egg-info/`、`uv.lock`（如锁定本地路径）
- [ ] `.env`、`.env.docker`、`.env.llmtest`（**含真实 API Key，绝不复制**）
- [ ] `csdn_publish.md`、`INTERVIEW_LOG.md`、`PROBLEMS_AND_SOLUTIONS.md` 等个人文档
- [ ] `eval_react_100.json`（旧评测数据，确认是否含真实工单；如含则脱敏或弃用）
- [ ] `data/feedback.db` 等运行时产物（.gitignore 已覆盖）

## 三、改造

- [ ] `.env` 三件套切 Hy3：`SUPPORT_AGENT_LLM_BASE_URL / REPLY_MODEL / API_KEY`（见 `.env.example`）
- [ ] 新增意图路由：健康类消息进入分诊管线（`app/` 内新模块）
- [ ] 新增红旗规则引擎：加载 `config/redflag_rules.json`，命中即强制急诊提示前置 + escalate
- [ ] 新增红线词校验：确诊/开药/剂量/人药模式检测，写入输出 `boundary_violations`
- [ ] 五区块结构化输出 schema（`app/models/schemas.py` 扩展）
- [ ] `streamlit_demo.py` 独立演示界面（不依赖 Java 商城）

## 四、开源前检查（逐项打勾）

- [ ] 全仓库扫描无硬编码密钥：`grep -rn "sk-" --include="*.py" --include="*.md" --include="*.json"` 为空
- [ ] `.gitignore` 覆盖 `.env`（本仓库已配好，复制后确认）
- [ ] README 顶部含「个人参赛作品｜犀牛鸟开源·混元大语言模型实战任务｜非腾讯官方项目」声明
- [ ] README 说明应用层基于本人此前开发的 PetHub 客服系统改造（归属清晰）
- [ ] 健康免责声明：README + 应用输出均有
- [ ] 干净环境复现：新建 venv → `pip install` → 配 `.env` → mock API + demo 跑通

## 五、zoomed（Java 商城）处理

**不进参赛仓库。** 仅在 2 分钟 demo 视频中出现（商城客服窗口 → Agent 对话的真实集成画面），
README「致谢与来源说明」中提一句"已集成于 PetHub 商城"即可。
