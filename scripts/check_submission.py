"""Read-only publication checks; never prints secret values."""
import json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    extra=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    found=[]
    pattern=re.compile(rb'sk-[A-Za-z0-9_-]{25,}')
    for name in set(tracked+extra):
        p=ROOT/name
        if not name or not p.is_file():continue
        if p.suffix.lower() in {'.mp4','.png','.gif','.jpg','.pdf'}:continue
        if pattern.search(p.read_bytes()):found.append(name)
    ignored=subprocess.run(['git','check-ignore','-q','.env'],cwd=ROOT).returncode==0
    if found or '.env' in tracked or not ignored:
        print('FAIL: credential file check',found);return 1
    final=ROOT/'results/final'
    required=['results.csv','summary.json','traces.jsonl','validation_summary.json','validation_inputs.jsonl','validation_traces.jsonl']
    missing=[p for p in required if not (final/p).is_file()]
    if missing:print('FAIL missing:',missing);return 1
    summary=json.loads((final/'summary.json').read_text(encoding='utf-8'))
    validation=json.loads((final/'validation_summary.json').read_text(encoding='utf-8'))
    if summary['cases']!=100 or validation['completed']!=48:
        print('FAIL incomplete experiments');return 1
    if any(v['scored']!=100 or v['fallback_or_error'] for v in summary['configurations'].values()):
        print('FAIL unresolved model errors');return 1
    print('PASS: no detected tracked credentials; 100 cases x 2 configurations; 48 validation judgements')
    videos=[p for p in ROOT.rglob('*') if p.suffix.lower() in {'.mp4','.gif','.webm'} and '.venv' not in p.parts and '.git' not in p.parts]
    print('Video artifacts:',len(videos),'(script alone does not satisfy demo requirement)')
    return 0
if __name__=='__main__':sys.exit(main())
