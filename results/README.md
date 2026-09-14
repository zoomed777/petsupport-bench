# 结果文件怎么看

这次正式实验放在 `final/`，包括完整评分、模型输出、调用记录和方法验证。[实验报告](../docs/experiment_report.md)只读取这里的当前运行指纹和对应最新记录。

`memory/` 是后来补的记忆功能验证，单独保存，没有并入原批量评分。`preflight/` 只是参数检查记录，不用于实验结论。

下面这些是早期开发文件，我保留它们方便回看，但没有采用其结论：

- 根目录的 A0/A1/A2/A3_results.csv、full_results.csv、旧 dashboard 图、vulnerability_map.md 和 prompt_patches.json：使用模拟数据生成。
- live_A3_results.csv：旧版运行缺少模型调用记录，槽位评分还曾读取参考标签，因此其中的 76.06 分和零闸门结果不可靠。

如果只想看这次提交的结果，从 `final/` 和 `memory/` 开始即可。
