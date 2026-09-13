"""Focused regressions for the submission path; no network calls."""
from unittest.mock import patch
from pathlib import Path
from app.triage.pipeline import TriagePipeline
from app.triage.slots import extract
from app.guardrails.redlines import check_output
from scripts.run_live_eval import as_eval_output
from eval.final_judge import judge

def test_eval_never_copies_gold_slots():
    r = TriagePipeline().run('我家狗刚吃了巧克力', species='dog')
    out = as_eval_output({'required_slots':['invented_slot']}, r)
    assert 'invented_slot' not in out['collected_slots']

def test_profile_and_followup():
    p = TriagePipeline()
    r = p.run('我家猫有点拉稀', profile={'species':'cat','age':'adult','weight_kg':4})
    assert 'age' not in [q['slot'] for q in r.ask_questions]
    answers={'duration':'今天','frequency':'两次','appetite':'正常','mental_state':'正常','symptom':'软便'}
    r = p.run('我家猫有点拉稀',profile={'species':'cat','age':'adult'},answers=answers)
    assert r.report and not r.ask_questions

def test_unsafe_model_field_is_not_displayed():
    p = TriagePipeline(llm_generate=lambda _: {'risk':'每次喂500毫克布洛芬'})
    r = p.run('我家狗吃了巧克力')
    assert '500' not in r.report.render_markdown()
    assert r.blocked_fields == ['risk']
    assert r.redflag['has_emergency'] is True

def test_failure_is_observable():
    def fail(_):
        raise TimeoutError()
    r=TriagePipeline(llm_generate=fail).run('我家狗吃了巧克力')
    assert r.report and r.llm_errors == ['TimeoutError']

def test_units_and_ingredient_capture():
    r=extract('狗体重10斤，误食一块巧克力')
    assert r.slots['weight_kg']==5
    assert r.slots['ingested_item']=='巧克力'

def test_negated_first_occurrence_does_not_hide_later_dose():
    assert 'no_dosage' in check_output('不要喂100毫克。可以每次喂500毫克。').violations

def test_judge_rejects_missing_dimension():
    class Client:
        def _complete(self,*_):
            return '{"scores":[4,4],"evidence":[],"gate":false}'
    import pytest
    with pytest.raises(ValueError):
        judge(Client(),{'user_message':'test'},'test',[])

def test_streamlit_followup_form():
    from streamlit.testing.v1 import AppTest
    with patch('app.triage.pipeline.TriagePipeline.from_environment',return_value=TriagePipeline()):
        a=AppTest.from_file(Path(__file__).resolve().parents[1]/'streamlit_demo.py',default_timeout=30).run()
        a.radio[0].set_value('离线规则').run()
        a.text_area[0].set_value('我家猫有点拉稀')
        next(b for b in a.button if b.label=='运行 Agent').click().run()
        assert not a.exception
        assert len(a.text_input)==3
        assert any(b.label=='提交补充信息' for b in a.button)
