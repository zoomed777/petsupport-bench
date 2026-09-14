"""从已保存的实验记录生成报告，保留原始分数和评审理由。"""
from __future__ import annotations
import json,statistics,sys
from datetime import date
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.triage.hy3_client import _json_object

def main():
    out=ROOT/'results/final'
    summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    validation=json.loads((out/'validation_summary.json').read_text(encoding='utf-8'))
    history=[json.loads(s) for s in (out/'traces.jsonl').read_text(encoding='utf-8').splitlines() if s]
    last={(r['case_id'],r['config']):r for r in history if r['run_hash']==summary['run_hash']}
    rows=list(last.values()); scored=[r for r in rows if 'verdict' in r]
    current_history=[r for r in history if r['run_hash']==summary['run_hash']]
    failed_attempts=sum(r.get('status')!='ok' for r in current_history)
    worst_repeat=max(validation['per_output'].items(),key=lambda item:item[1]['sd'])
    gate_audit=[]
    for r in scored:
        raw=_json_object(r['verdict']['raw'])
        gate_audit.append({'case_id':r['case_id'],'config':r['config'],
          'semantic_gate':raw['gate'],'rule_violations':r['verdict']['rule_violations'],
          'final_gate':r['verdict']['gate'],'semantic_D5':raw['scores'][4],
          'semantic_D5_reason':raw['evidence'][4]})
    (out/'gate_audit.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in gate_audit),encoding='utf-8')
    cases=[json.loads(s) for s in (ROOT/'data/cases_final.jsonl').read_text(encoding='utf-8').splitlines() if s]
    text=['# PetSupport-Bench 最终实验报告','',
      f'个人参赛作品｜报告整理日期：{date.today().isoformat()}。这份报告使用真实 Hy3 调用和规则评分的结果，原始记录在 [traces.jsonl](../results/final/traces.jsonl)。','',
      '这批实验对应提交 `6368460`。后来我修复了混合意图识别、调整了聊天界面，但没有重跑整套在线评测，所以这里仍是原实验版本的分数。','',
      '后面补的记忆 v2 做了单独验证，见[记忆管理](memory_management.md)，没有并入本报告评分。',
      '', '## 1. 我想检查的问题','',
      '我关注的是宠物商城咨询里混着健康描述的情况：系统能否注意到风险，问清缺少的信息，并说明自己能做什么。这类输入和回答都没有唯一写法，所以我用 Hy3 处理语言生成、部分意图判断和语义评分，再用规则检查已知紧急信号和用药红线。当前应用没有连接真实订单或人工接管系统。',
      '', '## 2. 样本从哪里来','',
      f'我使用了 {summary["cases"]} 条构造场景，共 {summary["unique_messages"]} 种不同问题表达。样本沿用开发期模板，包含档案变体和重复表达，不是 100 个独立真实用户，也不是兽医标注的病例。',
      '', '| 类别 | 数量 |','|---|---:|']
    for k,v in Counter(c['category'] for c in cases).items():text.append(f'| {k} | {v} |')
    text+=['','混合意图、诱导和长文本干扰共 25 条。参考分流等级是我为实验定义的工程标签，部分场景仍可能有争议。正式运行前修正了英文年龄占位符、物种冲突和缺失的知识 ID，记录在 `data/final_data_changes.json`。没有根据最后得分挑掉难例。','',
      '知识库最后保留了 8 条有具体来源的简短内容。原稿里缺少可靠依据的剂量阈值、统一禁食时间和固定疫苗日程已移除。模型补充了知识库以外的内容，不会仅因说得像常识就算作有证据支持。',
      '', '## 3. 方法与复现','',
      '- A0：给 Hy3 安全系统提示、原始消息和宠物档案，让它直接回复，不提供标签或参考答案。',
      '- A3：先处理档案和信息项，检查风险，必要时追问，再结合知识卡片调用 Hy3 整理文字；其中部分规则路径不调用模型。',
      '- 两组使用同一份七维标准和同一 Hy3 评分。评分模型只看候选文字和评审依据，不看配置名或应用自报的命中结果。',
      '- 参数为温度 0、`max_tokens=4096`、`thinking=disabled`。保存请求 ID、模型、用量、停止原因、输出、评分理由、错误和输入/代码指纹，方便检查。',
      '- 这批实验只评第一轮。聊天页面后来支持继续回答追问，但没有重新做完整多轮评测。A0 和 A3 同时改变了多个组件，也不能用两组差异单独解释某一个组件的作用。',
      '', '```powershell',
      '.\\.venv\\Scripts\\python.exe scripts/final_eval.py',
      '.\\.venv\\Scripts\\python.exe scripts/validate_final.py',
      '.\\.venv\\Scripts\\python.exe scripts/analyze_final.py','```',
      '', '## 4. 完整评测结果','', '| 配置 | 有效评分 | 平均分 | 闸门触发 | 生成调用次数 | 失败/回退 |','|---|---:|---:|---:|---:|---:|']
    for a,v in summary['configurations'].items():
        text.append(f'| {a} | {v["scored"]} | {v["mean"]} | {v["gate_count"]} | {v["generation_calls"]} | {v["fallback_or_error"]} |')
    text+=['','表里的生成次数不包含评分调用。A3 有些路径直接用规则回复，因此生成调用少于样本数。失败/回退列按各样本最新一次记录统计，早先失败的尝试另保留在原始记录里。','',
           f'当前版本共有 {len(current_history)} 条执行记录，其中失败或回退尝试 {failed_attempts} 条。汇总取每个样本、每个配置的最新记录；重试可能重新生成回答，所以表格反映的是包含成功重试后的质量，不是首次请求成功率。',
           '', '| 类别 | A0均分 | A3均分 |','|---|---:|---:|']
    for cat in dict.fromkeys(c['category'] for c in cases):
        values=[]
        for a in ['A0','A3']:
            group=[r['verdict']['total'] for r in scored if r['category']==cat and r['config']==a]
            values.append(round(statistics.mean(group),2) if group else '未完成')
        text.append(f'| {cat} | {values[0]} | {values[1]} |')
    text+=['','### 闸门是怎么触发的','',
      '| 配置 | 语义Judge闸门 | 规则命中输出 | 仅规则触发闸门 |','|---|---:|---:|---:|']
    for a in ['A0','A3']:
        group=[r for r in gate_audit if r['config']==a]
        text.append(f'| {a} | {sum(r["semantic_gate"] for r in group)} | {sum(bool(r["rule_violations"]) for r in group)} | {sum(bool(r["rule_violations"]) and not r["semantic_gate"] for r in group)} |')
    text+=['','回看低分回答时，我发现评分规则也会出错。规则只检查 6 个字符的否定窗口，较长的禁止用药句可能被误判。例如 WITHIN-24H-001/A0 中的“不可自行喂药或外敷人用药物”，模型评审给 D5=4，规则却把这一维改成了 0。逐条分歧见 [gate_audit.jsonl](../results/final/gate_audit.jsonl)。','',
      '因此，总闸门次数从 17 到 4，不能直接解释成危险回答减少。模型评分也不是人工标准，这个分歧会影响两组总分的比较。我保留了原规则和原分数，没有在看完结果后改分；下一步需要用独立校准样本检查句子里的否定关系，再做人工复核。',
      '', '## 5. 评分方法本身可靠吗','',
      f'我另外构造了 4 种风险场景的好、中、差回答，以及伪引用、评分指令注入等变体。每份回答评 3 次，计划 48 次，实际完成 {validation["completed"]} 次，输入和每次评分都已保存。','',
      f'好＞中＞差的严格排序在 {validation["strict_good_medium_bad"]}/{validation["ranked_scenarios"]} 个场景中成立。各档均分（good 好、medium 中、bad 差、attack 对抗）为：{json.dumps(validation["mean_by_tier"],ensure_ascii=False)}。','',
      f'同一回答重复评分的平均总体标准差是 {validation["mean_repeat_sd"]}。这里检查的是重复性，没有和人工评分对照；同一模型反复评，也可能稳定地出现同一种偏差。','',
      f'波动最大的是 {worst_repeat[0]}，三次得分 {worst_repeat[1]["scores"]}，标准差 {worst_repeat[1]["sd"]:.2f}。所以只看平均波动还不够，这一类边界回答仍不稳定。','',
      '对抗回答的逐次结果在 `results/final/validation_traces.jsonl`。这一小组明显好坏不同的回答能排对，并不说明细微质量差异也能排对，后面还需要更难的验证样本。',
      '', '## 6. 低分回答里发现的问题','','下面各取 A0、A3 总分最低的三条。输入、回答片段和评分理由直接摘自原记录，模型理由中有不一致或自相矛盾的地方，我也原样保留，便于检查评分器。以下是待分析的实验输出，不是供实际照护使用的建议。','']
    for a in ['A0','A3']:
        worst=sorted([r for r in scored if r['config']==a],key=lambda r:r['verdict']['total'])[:3]
        for r in worst:
            text += [f'### {a} / {r["case_id"]} / {r["verdict"]["total"]}分','',
                f'输入：{next(c["user_message"] for c in cases if c["case_id"]==r["case_id"])}','',
                '输出片段：'+r['output'][:220].replace('\n',' '),'',
                '评分理由原文：'+'；'.join(f'D{i+1}={s}: {r["verdict"]["evidence"][i]}' for i,s in enumerate(r['verdict']['scores']) if s<4),'']
    delta=summary['configurations']['A3']['mean']-summary['configurations']['A0']['mean']
    text+=['## 7. 这次结果说明了什么','',
      f'A3 与 A0 的平均分差为 {delta:+.2f}，这次完整方案没有在总分上胜出。样本包含重复表达，评分器也有误报，我没有把这点差异解释成统计显著。','',
      '回看回答，主要问题是知识覆盖小、关键词容易误判否定和历史叙述、通用风险模板会带入无关提醒，以及追问没有贴合具体场景。有限的正则也识别不了所有危险建议。生成和评分共用 Hy3，可能存在同模型偏好；目前没有人工或兽医一致性，也没有临床有效性验证。','',
      '早期 `results/A0_results.csv` 等四配置表、`results/full_results.csv` 和旧图是模拟结果。旧 `live_A3_results.csv` 缺少调用记录，评分还曾读取参考信息项，因此其中的 76.06 分和“零风险”结论没有采用。本报告只用 `results/final/` 的数据。',
      '', '## 8. 材料放在哪里','',
      '代码、运行配置样例、构造数据、评分标准、真实输出和验证记录都已放到仓库。我录的[演示视频](../demo/demo.mp4)是 67.57 秒，参考脚本在 `docs/demo_script.md`。仓库链接已经提交给主办方。密钥、本机数据为什么没有上传，以及旧模块和当前入口的关系，写在[代码与文件说明](submission_scope.md)。']
    (ROOT/'docs/experiment_report.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print('已从保存的实验记录生成报告')
if __name__=='__main__':main()
