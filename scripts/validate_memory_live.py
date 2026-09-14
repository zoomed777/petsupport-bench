"""Four constructed live memory turns; separate from frozen single-turn scores."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.chat_session import ChatSession
from app.triage.pipeline import TriagePipeline

def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    target=ROOT/'results/memory/live_validation.json'
    if target.exists():
        raise SystemExit('Existing validation preserved. Choose a new output before rerunning.')
    cases=[
        ('狗叫豆豆，3岁，体重6公斤，今天食欲不好，精神正常。','豆豆','3岁'),
        ('年龄说错了，不是3岁，是5岁，体重实际5公斤。','豆豆','5岁'),
        ('另一只猫叫咪咪，1岁，今天拉稀两次，食欲正常，精神正常。','咪咪','1岁'),
        ('回到豆豆，精神还可以。','豆豆','5岁'),
    ]
    chat=ChatSession()
    rows=[]
    for text,name,age in cases:
        pipeline=TriagePipeline.from_environment()
        if not pipeline.client:
            raise SystemExit('Hy3 configuration missing')
        reply=chat.reply(text,pipeline)
        passed=(chat.memory.active.name==name and chat.answers.get('age')==age
                and bool(reply['report']) and bool(reply['audit']['model_calls']) and not reply['audit']['errors'])
        rows.append({'input':text,'expected_pet':name,'expected_age':age,'passed':passed,'reply':reply})
        print(name,age,'PASS' if passed else 'FAIL',flush=True)
    target.parent.mkdir(exist_ok=True,parents=True)
    paths=['app/chat_session.py','app/memory.py','app/memory_store.py','app/triage/hy3_client.py']
    report={'created_at':datetime.now(timezone.utc).isoformat(),'source':'author-constructed engineering cases',
            'scope':'four live turns, not a clinical or batch rubric evaluation',
            'code_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths},
            'completed':len(rows),'passed':sum(row['passed'] for row in rows),
            'provider_calls':sum(len(row['reply']['audit']['model_calls']) for row in rows),'rows':rows}
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved:',target)
    if report['passed']!=4:
        raise SystemExit(2)

if __name__=='__main__':main()
