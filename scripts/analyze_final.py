"""Generate the submission report exclusively from trace-backed observations."""
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
      f'个人参赛作品｜报告生成日期：{date.today().isoformat()}。输出及评分由真实 Hy3 请求和程序规则产生，原始记录见 results/final/traces.jsonl。',
      '版本范围：本批实验对应提交6368460。后续混合意图识别的演示修复单独做回归验证，未重跑整套在线评测；本报告分数不代表修复版的新成绩。',
      '', '## 1. 场景与必要性','',
      '目标用户为宠物商城咨询者。研究订单/商品内容夹杂健康风险时，能否优先回应风险、收集未知信息并说明能力边界。自然语言表述和回复不存在唯一标准答案；Hy3 用于语言生成、低置信意图澄清和语义评审。规则负责已知红旗与用药红线。该原型未连接真实商城订单或实际人工接管系统。',
      '', '## 2. 数据来源和实验边界','',
      f'共 {summary["cases"]} 条作者构造场景，{summary["unique_messages"]} 种不同问题表达；不是患者数据或兽医标注。数据继承开发期模板，含档案变体和重复表达，因此不能视为100个独立真实用户。',
      '', '| 类别 | 数量 |','|---|---:|']
    for k,v in Counter(c['category'] for c in cases).items():text.append(f'| {k} | {v} |')
    text+=['','混合意图、诱导、长文本干扰共25条。参考分流等级是作者定义的工程政策，部分场景存在合理争议。修正了英文年龄占位符、部分物种冲突和缺失知识ID；变更记录见 data/final_data_changes.json。未依据最终得分筛选或删去难例。',
      '知识库仅保留8条有具体来源的简短事实，移除了原稿没有可靠依据的剂量阈值、统一禁食时长和固定疫苗日程。未覆盖的事实不能凭模型常识获得“有证据”高分。',
      '', '## 3. 方法与复现','',
      '- A0：同一Hy3、安全系统提示、原始消息及宠物档案，直接回复，不提供标签或参考答案。',
      '- A3：档案/槽位、红旗规则、必要时追问、知识卡片与Hy3文案增强；触发规则的部分路径不调用模型。',
      '- 所有结果由同一冻结七维rubric、同一Hy3 Judge审阅；Judge只见输出与评审依据，不见配置名和应用自报命中结果。',
      '- 温度0、max_tokens=4096、thinking=disabled。记录请求ID、返回模型、token用量、停止原因、输出、逐维理由、错误和输入/代码哈希。',
      '- 最终实验只评第一轮回复；交互演示支持继续填写问题。未将其称为多轮消融。A0/A3同时改变多个组件，不能由该对比得出单组件因果贡献。',
      '', '```powershell',
      '.\\.venv\\Scripts\\python.exe scripts/final_eval.py',
      '.\\.venv\\Scripts\\python.exe scripts/validate_final.py',
      '.\\.venv\\Scripts\\python.exe scripts/analyze_final.py','```',
      '', '## 4. 完整评测结果','', '| 配置 | 有效评分 | 平均分 | 闸门触发 | 生成调用次数 | 失败/回退 |','|---|---:|---:|---:|---:|---:|']
    for a,v in summary['configurations'].items():
        text.append(f'| {a} | {v["scored"]} | {v["mean"]} | {v["gate_count"]} | {v["generation_calls"]} | {v["fallback_or_error"]} |')
    text+=['','生成调用次数明确排除Judge调用；纯规则回复不能称为Hy3生成。错误/回退在表中单独披露，未偷偷替换为成功结果。',
           f'当前冻结版本共保留 {len(current_history)} 条执行记录，其中失败/回退尝试 {failed_attempts} 条。汇总按每个样本与配置的最新记录计分；重试可能重新生成候选回复，因此这是成功重试后的质量结果，不是首次请求成功率。历史失败仍可在原始记录中检查。',
           '', '| 类别 | A0均分 | A3均分 |','|---|---:|---:|']
    for cat in dict.fromkeys(c['category'] for c in cases):
        values=[]
        for a in ['A0','A3']:
            group=[r['verdict']['total'] for r in scored if r['category']==cat and r['config']==a]
            values.append(round(statistics.mean(group),2) if group else '未完成')
        text.append(f'| {cat} | {values[0]} | {values[1]} |')
    text+=['','### 闸门来源审计（不改动原评分）','',
      '| 配置 | 语义Judge闸门 | 规则命中输出 | 仅规则触发闸门 |','|---|---:|---:|---:|']
    for a in ['A0','A3']:
        group=[r for r in gate_audit if r['config']==a]
        text.append(f'| {a} | {sum(r["semantic_gate"] for r in group)} | {sum(bool(r["rule_violations"]) for r in group)} | {sum(bool(r["rule_violations"]) and not r["semantic_gate"] for r in group)} |')
    text+=['','**闸门次数不等于真实危险回答数量。** 审阅低分case发现，规则的6字符否定窗口会误报较长的禁止用药句，例如 WITHIN-24H-001/A0 的“不可自行喂药或外敷人用药物”；其语义Judge原判D5=4，但规则强制归零。语义Judge本身也不是人工金标准。逐条分歧见 results/final/gate_audit.jsonl。',
      '因此不能把17→4解释为真实安全风险下降，或据此断言A3更安全。这是评估器自身的失败模式，也可能影响两配置总分比较。为避免看完结果再调整规则，保留冻结评分，公开分歧；后续应在独立校准集验证句级否定识别并人工复核闸门。',
      '', '## 5. 评估方法有效性','',
      f'独立构造4种风险场景的好/中/差输出及伪引用、评分指令注入变体。每份输出调用Judge 3次，预期48次；实际完成 {validation["completed"]} 次。所有输入与逐次结果都保留。',
      f'严格好>中>差排序：{validation["strict_good_medium_bad"]}/{validation["ranked_scenarios"]} 个场景；各档均分：{json.dumps(validation["mean_by_tier"],ensure_ascii=False)}。',
      f'同一输出重复评分的平均总体标准差：{validation["mean_repeat_sd"]}。这测量重复性，不等于与人工一致，也不能排除同一模型共同偏差。',
      f'波动最大的输出为 {worst_repeat[0]}，3次得分 {worst_repeat[1]["scores"]}，标准差 {worst_repeat[1]["sd"]:.2f}。平均波动较小并不意味着所有边界样本都稳定。',
      '对抗变体的结果见validation_traces.jsonl，不能将增加字数或伪造知识ID作为加分证据。验证集规模很小，明显好坏差异的排序能力不能外推到细微质量差异。',
      '', '## 6. 典型失败与归因','']
    for a in ['A0','A3']:
        worst=sorted([r for r in scored if r['config']==a],key=lambda r:r['verdict']['total'])[:3]
        for r in worst:
            text += [f'### {a} / {r["case_id"]} / {r["verdict"]["total"]}分','',
                f'输入：{next(c["user_message"] for c in cases if c["case_id"]==r["case_id"])}','',
                '输出片段：'+r['output'][:220].replace('\n',' '),'',
                '低分原因：'+'；'.join(f'D{i+1}={s}: {r["verdict"]["evidence"][i]}' for i,s in enumerate(r['verdict']['scores']) if s<4),'']
    delta=summary['configurations']['A3']['mean']-summary['configurations']['A0']['mean']
    text+=['## 7. 结论与限制','',
      f'本次A3与A0平均分差为 {delta:+.2f}。结果按实际数据报告；不预设完整方案必然胜出，不将差异称为统计显著。',
      '已知限制：知识覆盖窄；关键词可能误判否定/历史叙述；所有急症共用模板会产生无关提醒；资料不足时规则追问可能遗漏具体场景槽位；有限正则无法识别全部危险建议。候选生成和评审共用Hy3，存在同模型偏好；未做人类/兽医一致性和临床有效性验证。',
      '原 results/A0_results.csv 等四配置表、results/full_results.csv、旧图表均为开发期模拟，不是模型实验。旧live_A3_results.csv缺少调用审计且评分曾引用标准槽位，不能引用其76.06分或“零风险”结论。本报告仅引用results/final中的新数据。',
      '', '## 8. 交付状态','',
      '代码、依赖、构造数据、冻结rubric、真实输出与评分、判别力/重复性/对抗验证、分析报告均在仓库。演示视频由参赛者自行录制，docs/demo_script.md是脚本而非视频；正式提交前需附不超过2分钟的视频或GIF，并在活动入口提交仓库链接。']
    (ROOT/'docs/experiment_report.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print('Report generated from audited results')
if __name__=='__main__':main()
