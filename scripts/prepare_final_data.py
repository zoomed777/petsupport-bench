"""Freeze legacy constructed cases with mechanical metadata repairs, not model labels."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def main():
    kb = {json.loads(s)["kb_id"] for s in (ROOT/"data/knowledge_base.jsonl").read_text(encoding="utf-8").splitlines() if s}
    rows, edits = [], []
    for line in (ROOT/"data/cases_v2.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        for old,new in [("puppy_kitten","幼年"),("juvenile","幼年"),("adult","成年"),("senior","老年")]:
            c["user_message"] = c["user_message"].replace(old,new)
        # Explicit species in templates takes precedence over random profile.
        msg = c["user_message"]
        if "我家猫" in msg or "猫砂盆" in msg:
            c["pet_profile"]["species"] = "cat"
        elif "我家狗" in msg:
            c["pet_profile"]["species"] = "dog"
        # Remove unsupported precise treatment instructions from annotation.
        c["must_remind"] = [s for s in c.get("must_remind",[]) if "禁食" not in s and "24h内可致命" not in s]
        removed = [s for s in c.get("kb_refs",[]) if s not in kb]
        c["kb_refs"] = [s for s in c.get("kb_refs",[]) if s in kb]
        if removed:
            edits.append({"case_id":c["case_id"],"unavailable_kb_refs_removed":removed})
        c["label_provenance"] = "author-constructed engineering policy; not veterinary gold standard"
        rows.append(c)
    (ROOT/"data/cases_final.jsonl").write_text("".join(json.dumps(c,ensure_ascii=False)+"\n" for c in rows),encoding="utf-8")
    (ROOT/"data/final_data_changes.json").write_text(json.dumps(edits,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Frozen {len(rows)} constructed cases; {len(set(c['user_message'] for c in rows))} distinct messages")
if __name__ == "__main__":
    main()
