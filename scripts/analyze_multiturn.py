"""Build a separate engineering report; never rewrite frozen rubric scores."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = ['multiturn_v1_baseline_offline', 'multiturn_v1_auditfix_offline', 'multiturn_v1_auditfix_live']
    summaries = [json.loads((ROOT / 'results' / name / 'summary.json').read_text(encoding='utf-8')) for name in names]
    inputs = [json.loads((ROOT / 'results' / name / 'inputs.json').read_text(encoding='utf-8')) for name in names]
    if any(value != inputs[0] for value in inputs[1:]):
        raise ValueError('Comparison needs identical scenario inputs and expectations')
    cases = inputs[0]['cases']
    expected_checks = sum(len(step['checks']) for case in cases for step in case['steps'])
    tables = [{r['case_id']: r for r in summary['scenarios']} for summary in summaries]
    live_rows = [json.loads(line) for line in (ROOT / 'results' / names[-1] / 'traces.jsonl').read_text(encoding='utf-8').splitlines() if line]
    latest = {row['case_id']: row for row in live_rows}
    provider_calls = [call for row in latest.values() for turn in row['turns'] for call in turn['calls']]
    historical_calls = [call for row in live_rows for turn in row['turns'] for call in turn['calls']]
    models = sorted({call['provider']['model'] for call in provider_calls if call.get('provider') and call['provider'].get('model')})
    tokens = sum(call.get('provider', {}).get('usage', {}).get('total_tokens', 0)
                 for call in provider_calls if call.get('provider'))
    unique = len({step['input'] for case in cases for step in case['steps']})
    report = ['# 多轮记忆补充测试', '',
        '原来的记忆验证只有四轮，所以我又补了一组多轮工程案例，检查宠物切换、纠错和上下文是否按预期工作。这份结果单独保存，没有改原来的七维评分和视频。', '',
        '## 一、这次测什么', '',
        f'共 {len(cases)} 组构造对话、{sum(len(c["steps"]) for c in cases)} 轮输入、{unique} 种不同消息文本，定义了 {sum(len(t["checks"]) for c in cases for t in c["steps"])} 个可检查条件。长历史组连续补充信息，专门触发近期原文折叠。', '',
        '每一轮写明应该保留的名字、年龄、体重或症状，也检查不该出现的旧信息、追问状态、事件清理、上下文长度和已知风险提示。断言主要检查结构化状态和可观察的交互，不是重新给整段健康建议打质量分。', '',
        '样本由我根据功能和容易出错的情况构造，先固定输入与检查条件，再跑修复前版本。修复后仍使用同一组输入。这是针对已发现问题的回归测试，不是独立留出集，也不是真实用户或兽医验证。', '',
        '## 二、修复前后结果', '',
        '| 运行 | 完成组数 | 通过组数 | 通过轮数 | 通过检查项 | SDK 调用数 | 调用错误 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for label, result in zip(['修复前，离线', '修复后，离线', '修复后，Hy3 在线'], summaries):
        report.append(f'| {label} | {result["completed_scenarios"]}/{result["expected_scenarios"]} | {result["passed_scenarios"]}/{result["expected_scenarios"]} | {result["passed_turns"]}/{result["expected_turns"]} | {result["passed_checks"]}/{expected_checks} | {result["provider_calls"]} | {result["provider_errors"]} |')
    report += ['', f'上表按每个场景最近一次尝试汇总。最终版在线目录一共保留 {len(live_rows)} 次场景尝试、{len(historical_calls)} 次 SDK 调用，其中 {sum(c["status"] != "ok" for c in historical_calls)} 次错误；失败场景从第一轮重建后再试，因此最新汇总不是所有历史请求的总量。', '',
        '最终版在长历史组 MT12 中途遇到接口错误，续跑仍失败。之后单独做的最小连通性检查返回 HTTP 402，错误内容包含 quota/额度，记录见[接口诊断](../results/hy3_connectivity_20260914.json)。因此最终版只完整完成了 11 组在线测试，MT12 未完成，MT13、MT14 未运行，不能写成最终版本在线全部通过。', '',
        '在审计快照修复前的中间版本，14 组、55 轮、151 个条件曾全部通过，实际调用 42 次、错误 0 次。该记录仍在 `results/multiturn_v1_live/`；最终快照修复只改变历史记录的复制方式，但代码版本已变，所以这里仍把两次验证分开报告。']
    report += ['', f'在线返回的模型标识为 `{", ".join(models)}`，已记录的总 token 用量为 {tokens}。调用次数来自实际请求记录，不把每轮对话都算成模型调用，也不重复累加应用内的展示记录。SDK 配置允许单次请求内部重试一次，统计的是 SDK 调用，不是底层 HTTP 尝试数。', '',
        '| 案例 | 检查内容 | 修复前离线 | 修复后离线 | 修复后在线 |', '|---|---|---|---|---|']
    csv_rows = []
    for case in cases:
        statuses = [('通过' if table[case['case_id']]['passed'] else '失败' if table[case['case_id']]['status'] == 'ok' else '未完成') for table in tables]
        report.append('| ' + ' | '.join([case['case_id'], case['description'], *statuses]) + ' |')
        csv_rows.append({'case_id': case['case_id'], 'category': case['category'], 'description': case['description'],
                         'baseline_offline': statuses[0], 'fixed_offline': statuses[1], 'fixed_live': statuses[2]})
    report += ['', '## 三、两处具体问题怎么改的', '',
        '第一处是名字包含关系。先登记“豆豆”，再说“另一只狗叫小豆豆”，旧代码按子串匹配，把新宠物归到了豆豆名下，连年龄和症状也更新错了。现在先识别明确声明的新名字，同一位置优先匹配较长名字；两只宠物确实同名时，要求换一个可区分的称呼。', '',
        '第二处是待确认消息的归属。已经登记两只狗后，用户说“我家狗吐了两次”，系统会先问是哪只。如果下一句改聊猫，旧代码仍把待确认的狗消息拼了进去。现在只有明确回到兼容的已有宠物时才合并；切到新宠物会清掉待处理的归属消息，不把它写入新档案。', '',
        '修复前的失败记录没有删除。两个问题都用原输入复测，并加进自动测试。修复前只做了离线基线，因此不能把这里的前后差异解释成模型能力提升。', '',
        '最后复查还发现，每轮回复的部分审计信息引用了可变的记忆对象，后续更新会影响较早轮次的审计字段。现在返回独立快照，并增加历史回复不可变的回归测试；上表的修复后结果使用这一最终版本。', '',
        '中间版本的 `results/multiturn_v1_fixed_offline/` 和 `results/multiturn_v1_live/` 也保留了。它们的 `snapshot` 和当轮检查结果在执行时已复制，但 `reply.audit` 内部分记忆字段可能被后续轮次改写，不应用来还原当轮审计状态。原始请求、响应和当轮断言未因此改变。最终在线验证另存在 `results/multiturn_v1_auditfix_live/`，不是修改旧记录得到的。', '',
        '## 四、评测记录怎么保护', '',
        '新验证脚本会先计算输入、代码、评分提示词或应用模块、知识库和运行设置的指纹。在线配置包含模型名、接口地址的哈希和主要调用参数，不保存密钥。输入、代码或设置变化后必须使用新目录，不能按相同案例编号直接套用旧记录。', '',
        '目录里保存 manifest.json、inputs.json、逐条 traces 和汇总。没有版本说明的旧目录、被修改的输入快照、指纹不符的记录以及不完整的 JSON 行都会被拒绝。同一目录同时只允许一个写入进程。', '',
        '成功完成的案例会复用，包括检查不通过的案例，不为了得到通过结果反复重跑；调用失败可在同一版本下续跑，多轮场景需要从该场景第一轮重建状态。失败尝试一直保留。中断遗留锁时先确认原进程已结束，最简单的办法是换新目录。', '',
        '## 五、如何查看与复跑', '',
        '- [构造案例和逐轮检查条件](../data/multiturn_eval_v1.json)',
        '- [修复前离线结果](../results/multiturn_v1_baseline_offline/summary.json)',
        '- [修复后离线结果](../results/multiturn_v1_auditfix_offline/summary.json)',
        '- [在线结果](../results/multiturn_v1_auditfix_live/summary.json) · [原始对话与请求](../results/multiturn_v1_auditfix_live/traces.jsonl)',
        '- [按场景对比的 CSV](../results/multiturn_comparison.csv)', '',
        '```powershell',
        '.\\.venv\\Scripts\\python.exe scripts/eval_multiturn.py --mode offline --out results/my_multiturn_offline',
        '.\\.venv\\Scripts\\python.exe scripts/eval_multiturn.py --mode live --out results/my_multiturn_live --max-calls 80',
        '.\\.venv\\Scripts\\python.exe scripts/validate_final.py --out results/my_rubric_validation',
        '```', '',
        '在线运行需要有效 Hy3 配置，也会消耗额度。多轮脚本默认最多 80 次 SDK 调用，遇到调用错误会停下并保存已完成部分。上面最后一条是原四场景评分方法的重新验证，和多轮记忆测试不同；本次只修了它的缓存保护，没有重新生成一批七维评分。', '',
        '本机恢复额度后，如果代码和配置未变，可使用 `--mode live --out results/multiturn_v1_auditfix_live --max-calls 40` 续跑当前未完成部分。不要重新提交密钥到仓库或聊天中；只在本机配置。若指纹已变化，改用新目录。', '',
        '## 六、还有哪些不足', '',
        '这一组通过不代表所有名字、别名、指代和纠错都可靠。检查条件是我自己写的，且用于定位和修复问题，因此回归通过率不能代表真实用户成功率。离线结果只反映规则与状态管理，在线结果也只检查列出的条件，没有兽医人工复核或临床结论。', '',
        '原评分器的长否定句误报仍然保留在原实验中。本次没有宣称已经完成评分规则的独立校准；那需要另外准备校准集、留出集和人工复核。']
    (ROOT / 'docs/multiturn_report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    with (ROOT / 'results/multiturn_comparison.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    print('Generated docs/multiturn_report.md and results/multiturn_comparison.csv')


if __name__ == '__main__':
    main()
