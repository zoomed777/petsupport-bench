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

def test_streamlit_chat_followup():
    from streamlit.testing.v1 import AppTest
    with patch('app.triage.pipeline.TriagePipeline.from_environment',return_value=TriagePipeline()):
        a=AppTest.from_file(Path(__file__).resolve().parents[1]/'streamlit_demo.py',default_timeout=30).run()
        a.radio[0].set_value('离线规则').run()
        a.chat_input[0].set_value('我家猫有点拉稀').run()
        assert not a.exception
        assert not a.text_input and not a.selectbox
        assert len(a.session_state['chat_session'].pending)==3
        a.chat_input[0].set_value('成年3岁，今天上午开始，拉了两次，食欲正常，精神正常。').run()
        assert not a.exception
        assert not a.session_state['chat_session'].pending
        assert len(a.session_state['chat_messages'])==4


def test_mixed_appetite_description_is_not_dropped():
    from app.triage.router import classify
    for symptom in ['食欲不好', '胃口不佳', '吃得很少', '吐了', '干呕']:
        message='换的猫粮什么时候到？我家猫最近'+symptom
        assert classify(message).intent.value == 'mixed'
        result=TriagePipeline().run(message)
        assert result.ask_questions or result.report
        assert result.collected_slots.get('symptom')
    result=TriagePipeline().run('换的猫粮什么时候到？我家猫最近食欲不好')
    assert result.collected_slots['symptom'] == ['anorexia']
    assert [q['slot'] for q in result.ask_questions] == ['age', 'duration', 'mental_state']
    assert classify('换的猫粮什么时候到？').intent.value == 'order'


def test_screenshot_case_has_visible_feedback_and_followup():
    from streamlit.testing.v1 import AppTest
    with patch('app.triage.pipeline.TriagePipeline.from_environment',return_value=TriagePipeline()):
        a=AppTest.from_file(Path(__file__).resolve().parents[1]/'streamlit_demo.py',default_timeout=30).run()
        a.radio[0].set_value('离线规则').run()
        a.chat_input[0].set_value('换的猫粮什么时候到？我家猫最近食欲不好').run()
        assert not a.exception
        assert any('订单和健康两件事' in item.value for item in a.markdown)
        assert not a.selectbox and not a.text_input
        a.chat_input[0].set_value('它3岁，今天上午开始，精神正常').run()
        assert not a.exception
        assert not a.text_input
        assert any('食欲下降' in item.value for item in a.markdown)
        next(b for b in a.button if b.label=='开始新对话').click().run()
        assert a.session_state['chat_messages']==[]
        assert a.session_state['chat_session'].user_turns==[]


def test_chat_does_not_accept_invented_followup_facts():
    from app.chat_session import ChatSession
    chat=ChatSession()
    chat.reply('我家猫食欲不好',TriagePipeline())
    class Client:
        calls=[]
        def _complete(self,*_):
            return '{"answers":[{"slot":"age","quote":"成年"},{"slot":"mental_state","quote":"正常"}]}'
    p=TriagePipeline()
    p.client=Client()
    chat.reply('不知道年龄，精神正常',p)
    assert 'age' not in chat.answers
    assert chat.answers['mental_state']=='正常'
    assert 'age' in [q['slot'] for q in chat.pending]


def test_chat_emergency_interrupts_followup_without_extraction():
    from app.chat_session import ChatSession
    chat=ChatSession()
    chat.reply('我家猫拉稀',TriagePipeline())
    class Client:
        calls=[]
        def _complete(self,*_):
            raise AssertionError('Emergency should bypass follow-up extraction')
    p=TriagePipeline()
    p.client=Client()
    reply=chat.reply('现在张着嘴呼吸，喘不上气',p)
    assert reply['report'] and '立即就医' in reply['content']
    assert not chat.pending
    assert not reply['audit']['errors']


def test_chat_partial_answer_does_not_require_a_form():
    from app.chat_session import ChatSession
    chat=ChatSession()
    chat.reply('换的猫粮什么时候到？我家猫最近食欲不好',TriagePipeline())
    reply=chat.reply('3岁',TriagePipeline())
    assert chat.answers['age']=='3岁'
    assert [q['slot'] for q in chat.pending]==['duration','mental_state']
    assert reply['report'] is None
