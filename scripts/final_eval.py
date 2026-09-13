"""Auditable live A0/A3 evaluation; append-only traces and resumable failures."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.triage.hy3_client import Hy3TriageClient
from app.triage.pipeline import TriagePipeline, load_kb
from eval.final_judge import judge, PROMPT

BASELINE = """你是宠物商城健康沟通助手。根据用户描述提供中文回复，支持健康与订单混合意图。
不确诊、不开药、不提供药物剂量。必要时建议联系兽医。不编造订单状态。
健康报告请分风险、行动、禁止事项、就诊摘要、证据/不确定性五区块；信息不足时可追问。
本轮只输出最终回复。"""
LOCK = threading.Lock()
NEXT = 0.0
ORIGINAL = Hy3TriageClient._complete
def paced(self,*a,**kw):
    global NEXT
    with LOCK:
        now = time.monotonic()
        pause = max(0,NEXT-now)
        NEXT = max(now,NEXT)+1.35
    if pause:
        time.sleep(pause)
    return ORIGINAL(self,*a,**kw)
Hy3TriageClient._complete = paced

def fingerprint():
    paths = [ROOT/"data/cases_final.jsonl",ROOT/"data/knowledge_base.jsonl",PROMPT,Path(__file__)]
    paths += sorted((ROOT/"app/triage").glob("*.py")) + sorted((ROOT/"app/guardrails").glob("*.py"))
    paths += [ROOT/"eval/final_judge.py",ROOT/"config/redflag_rules.json"]
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()

def app_output(case):
    p = TriagePipeline.from_environment()
    if not p.client:
        raise RuntimeError("Hy3 configuration missing")
    r = p.run(case["user_message"],profile=case.get("pet_profile"))
    if r.report:
        text = r.report.render_markdown()
    elif r.ask_questions:
        text = "需要补充信息：\n"+"\n".join(q["question"] for q in r.ask_questions)
        text += "\n不提供诊断或药物剂量。如出现呼吸困难、抽搐或不能排尿，请立即联系急诊兽医，不要等待答完问题。"
    else:
        text = "此页面仅处理健康沟通，未连接真实订单系统。请准备订单号或商品详情并联系商城人工客服核实；此处无法确认发货状态。"
    return text, {"generation_calls":r.llm_calls,"errors":r.llm_errors,
                  "collected_slots":r.collected_slots,"questions":r.ask_questions,
                  "blocked_fields":r.blocked_fields,"redflag":r.redflag}

def one(case,config,knowledge,run_hash):
    entry = {"case_id":case["case_id"],"category":case["category"],"config":config,
             "run_hash":run_hash,"timestamp":datetime.now(timezone.utc).isoformat()}
    try:
        if config == "A0":
            client = Hy3TriageClient.from_env()
            if client is None:
                raise RuntimeError("Hy3 configuration missing")
            # Only observable input. NO labels, required slots or ideal answer.
            text = client._complete(BASELINE,json.dumps({k:case.get(k) for k in ["user_message","pet_profile"]},ensure_ascii=False))
            trace = {"generation_calls":client.calls,"errors":[]}
        else:
            text,trace = app_output(case)
        entry.update(output=text,trace=trace)
        grader = Hy3TriageClient.from_env()
        verdict = judge(grader,case,text,knowledge)
        entry.update(verdict=verdict,judge_calls=grader.calls,status="ok" if not trace["errors"] else "fallback")
    except Exception as exc:
        entry.update(status="error",error=type(exc).__name__)
    return entry

def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap=argparse.ArgumentParser()
    ap.add_argument("--limit",type=int,default=100)
    ap.add_argument("--workers",type=int,default=4)
    ap.add_argument("--out",default="results/final")
    args=ap.parse_args()
    out=ROOT/args.out
    out.mkdir(parents=True,exist_ok=True)
    cases=[json.loads(s) for s in (ROOT/"data/cases_final.jsonl").read_text(encoding="utf-8").splitlines() if s][:args.limit]
    knowledge=list(load_kb().values())
    run_hash=fingerprint()
    path=out/"traces.jsonl"
    saved=[json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s] if path.exists() else []
    done={(r["case_id"],r["config"]):r for r in saved if r.get("status")=="ok" and r.get("run_hash")==run_hash}
    jobs=[(c,a) for c in cases for a in ["A0","A3"] if (c["case_id"],a) not in done]
    print(f"Running {len(jobs)} remaining tasks; {len(done)} cached",flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool, path.open("a",encoding="utf-8") as f:
        futures=[pool.submit(one,c,a,knowledge,run_hash) for c,a in jobs]
        for future in as_completed(futures):
            r=future.result()
            f.write(json.dumps(r,ensure_ascii=False)+"\n");f.flush()
            done[(r["case_id"],r["config"])]=r
            print(r["case_id"],r["config"],r["status"],flush=True)
    rows=[]
    for c in cases:
        for a in ["A0","A3"]:
            r=done[(c["case_id"],a)]
            v=r.get("verdict",{})
            rows.append({"case_id":c["case_id"],"category":c["category"],"config":a,"status":r["status"],
                         **{f"D{i+1}":s for i,s in enumerate(v.get("scores",[None]*7))},
                         "total":v.get("total"),"gate":v.get("gate"),
                         "generation_calls":len(r.get("trace",{}).get("generation_calls",[]))})
    with (out/"results.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary={"run_hash":run_hash,"cases":len(cases),"unique_messages":len(set(c["user_message"] for c in cases)),
             "model":"hy3","temperature":0,"max_tokens":4096,"thinking":"disabled","configurations":{}}
    for a in ["A0","A3"]:
        scored=[r for r in rows if r["config"]==a and r["total"] is not None]
        summary["configurations"][a]={"scored":len(scored),"mean":round(statistics.mean(r["total"] for r in scored),2) if scored else None,
            "gate_count":sum(r["gate"] for r in scored),"generation_calls":sum(r["generation_calls"] for r in scored),
            "fallback_or_error":sum(r["status"]!="ok" for r in rows if r["config"]==a)}
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if any(r["status"]!="ok" for r in rows):
        raise SystemExit(2)
if __name__=="__main__":
    main()
