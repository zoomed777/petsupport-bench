# 结果文件怎么看

这次正式实验放在 `final/`，包括完整评分、模型输出、调用记录和方法验证。[实验报告](../docs/experiment_report.md)只读取这里的当前运行指纹和对应最新记录。

`memory/` 是后来补的记忆功能验证，单独保存，没有并入原批量评分。`preflight/` 只是参数检查记录，不用于实验结论。

新增的 `multiturn_v1_baseline_offline/`、`multiturn_v1_auditfix_offline/` 和 `multiturn_v1_auditfix_live/` 分别是 14 组、55 轮测试的修复前离线、最终版离线和最终版在线记录。最终版在线只完整完成 11 组，剩余部分因 HTTP 402 额度问题未完成。输入快照、代码指纹与逐轮原文都在各自目录；对比表为 [multiturn_comparison.csv](multiturn_comparison.csv)，分析见[多轮报告](../docs/multiturn_report.md)。

多轮中间版本 `multiturn_v1_fixed_offline/` 和 `multiturn_v1_live/` 也保留了。其中早期 `reply.audit` 的部分字段可能随后续记忆变化；当轮的独立 `snapshot`、检查结果及原始请求仍保留。最终结果请看带 `auditfix` 的目录，详见多轮报告。

下面这些是早期开发文件，我保留它们方便回看，但没有采用其结论：

- 根目录的 A0/A1/A2/A3_results.csv、full_results.csv、旧 dashboard 图、vulnerability_map.md 和 prompt_patches.json：使用模拟数据生成。
- live_A3_results.csv：旧版运行缺少模型调用记录，槽位评分还曾读取参考标签，因此其中的 76.06 分和零闸门结果不可靠。

如果只想看原单轮评分，从 `final/` 开始；新版记忆看多轮报告，`memory/` 保留初版四轮验证。
