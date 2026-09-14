from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from app.chat_session import ChatSession
from app.memory import Memory, Pet, units, MAX_CONTEXT_UNITS
from app.memory_store import MemoryStore
from app.triage.pipeline import TriagePipeline

def send(chat, text):
    return chat.reply(text,TriagePipeline())

def test_numeric_correction_removes_old_values_from_prompt():
    chat=ChatSession()
    send(chat,'我家狗叫豆豆，3岁，体重10公斤，今天食欲不好，精神正常')
    send(chat,'年龄说错了，不是3岁，是5岁，体重实际8公斤')
    pet=chat.memory.active
    assert pet.known_profile()['age']=='5岁'
    assert pet.known_profile()['weight_kg']==8
    context=pet.context('请继续')
    assert '3岁' not in context and '10公斤' not in context
    assert '5岁' in context
    assert len(pet.changes)>=2

def test_pet_switch_does_not_mix_profiles_or_symptoms():
    chat=ChatSession()
    send(chat,'我家狗叫豆豆，3岁，今天呕吐两次')
    dog=chat.memory.active
    send(chat,'另一只猫叫咪咪，1岁，今天食欲不好')
    cat=chat.memory.active
    assert cat.id!=dog.id and cat.species=='cat'
    assert cat.known_profile()['age']=='1岁'
    assert 'vomiting' not in cat.facts.get('symptom',[])
    send(chat,'回到豆豆，精神正常')
    assert chat.memory.active.id==dog.id
    assert chat.answers['age']=='3岁'
    assert 'vomiting' in chat.answers['symptom']

def test_two_dogs_require_name_when_ambiguous():
    chat=ChatSession()
    send(chat,'狗叫豆豆，3岁，食欲不好')
    send(chat,'另一只狗叫旺财，5岁，精神正常')
    response=send(chat,'我家狗今天吐了两次')
    assert '哪一只' in response['content']
    assert all('vomiting' not in pet.facts.get('symptom',[]) for pet in chat.memory.pets.values())
    send(chat,'是豆豆')
    assert chat.memory.active.name=='豆豆'
    assert chat.memory.active.facts['frequency']=='两次'

def test_multiple_pets_in_one_message_not_assigned():
    chat=ChatSession()
    response=send(chat,'猫吐了，狗食欲不好')
    assert '多只宠物' in response['content']
    assert not chat.memory.pets

def test_new_event_keeps_only_stable_profile():
    chat=ChatSession()
    send(chat,'狗叫豆豆，3岁，今天呕吐两次')
    old_event=chat.memory.active.event_id
    send(chat,'豆豆上次已经好了，新情况是今天拉稀')
    pet=chat.memory.active
    assert pet.event_id!=old_event
    assert pet.known_profile()['age']=='3岁'
    assert pet.facts['symptom']==['diarrhea']
    assert 'frequency' not in pet.facts

def test_support_topic_suspends_health_context():
    chat=ChatSession()
    send(chat,'狗叫豆豆，刚吃了巧克力')
    response=send(chat,'先说订单，我的快递什么时候到？')
    assert response['report'] is None and response['audit']['intent']=='order'
    assert chat.memory.active.risks
    assert chat.memory.topic=='support'

def test_long_history_compacts_but_keeps_risk_and_profile():
    chat=ChatSession()
    send(chat,'狗叫豆豆，3岁，刚吃了巧克力')
    for _ in range(35):
        send(chat,'精神正常，正在联系医院')
    pet=chat.memory.active
    assert len(pet.recent)<=6 and pet.folded_turns>=30
    assert pet.known_profile()['age']=='3岁'
    assert pet.risks
    assert units(pet.context('继续'))<=MAX_CONTEXT_UNITS
    assert '巧克力' in pet.context('继续')

def test_oversize_input_rejected_without_losing_emergency():
    chat=ChatSession()
    reply=send(chat,'狗吃了巧克力。'+'描述'*4000)
    assert '立即就医' in reply['content'] and '过长' in reply['content']
    assert not chat.memory.pets

def test_model_budget_checks_system_and_user_before_request():
    from app.triage.hy3_client import Hy3TriageClient
    client=Hy3TriageClient('https://example.invalid/v1','not-a-real-key','hy3')
    with patch.object(client.client.chat.completions,'create') as create:
        with pytest.raises(ValueError,match='Budget'):
            client._complete('系统'*10000,'用户')
        create.assert_not_called()
    client.client.close()

def test_stale_age_and_weight_are_not_reused():
    pet=Pet(name='豆豆',species='dog')
    for key,value in [('species','dog'),('age','3岁'),('weight_kg',4)]:
        pet.put(key,value,str(value))
        pet.profile[key]['updated_at']=(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()
    assert pet.known_profile()=={'species':'dog'}

def test_sqlite_roundtrip_and_new_conversation_isolation(tmp_path):
    store=MemoryStore(tmp_path/'memory.sqlite3')
    chat=ChatSession()
    send(chat,'狗叫豆豆，3岁，今天食欲不好')
    store.save('session-a',chat.memory,[{'role':'user','content':'example'}])
    restored=ChatSession(memory=Memory.from_dict(store.load('session-a')['memory']))
    assert restored.memory.active.name=='豆豆'
    assert restored.answers['age']=='3岁' and restored.pending
    new=ChatSession()
    for p in store.profiles():
        pet=Pet(**p)
        new.memory.pets[pet.id]=pet
    send(new,'豆豆今天拉稀')
    assert new.answers['age']=='3岁'
    assert new.answers['symptom']==['diarrhea']
    with pytest.raises(KeyError):
        store.load('unrelated-id')
    store.clear()
    assert store.profiles()==[] and store.list_sessions()==[]

def test_new_conversation_resets_episode_but_keeps_profile():
    chat=ChatSession()
    send(chat,'狗叫豆豆，3岁，刚吃了巧克力')
    fresh=chat.new_conversation()
    assert not fresh.user_turns and not fresh.pending
    assert len(fresh.memory.pets)==1
    pet=next(iter(fresh.memory.pets.values()))
    assert pet.known_profile()['age']=='3岁' and not pet.risks and not pet.facts

def test_explicit_species_correction_updates_same_pet():
    chat=ChatSession()
    send(chat,'猫叫豆豆，3岁，食欲不好')
    pet_id=chat.memory.active_id
    send(chat,'刚才说错了，不是猫，是狗')
    assert chat.memory.active_id==pet_id and chat.answers['species']=='dog'

def test_symptom_correction_not_combined_with_old_symptom():
    chat=ChatSession()
    send(chat,'狗叫豆豆，今天呕吐')
    send(chat,'说错了，不是呕吐，是拉稀')
    assert chat.answers['symptom']==['diarrhea']
    assert '呕吐' not in chat.memory.active.context('继续')

def test_ui_memory_is_opt_in_and_can_resume(tmp_path):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'local.sqlite3'
    def local_store():
        return MemoryStore(path)
    with patch('app.memory_store.MemoryStore',side_effect=local_store):
        app=AppTest.from_file(Path(__file__).resolve().parents[1]/'streamlit_demo.py',default_timeout=30).run()
        app.radio[0].set_value('离线规则').run()
        assert not path.exists()
        next(c for c in app.checkbox if c.label=='在本机保存宠物档案和会话').check().run()
        app.chat_input[0].set_value('我家狗叫豆豆，3岁，今天食欲不好').run()
        assert not app.exception
        stored=MemoryStore(path).list_sessions()
        assert len(stored)==1
        next(b for b in app.button if b.label=='开始新对话').click().run()
        app.selectbox[0].set_value(stored[0][0]).run()
        next(b for b in app.button if b.label=='恢复所选会话').click().run()
        assert not app.exception
        assert app.session_state['chat_session'].memory.active.name=='豆豆'
        fresh=AppTest.from_file(Path(__file__).resolve().parents[1]/'streamlit_demo.py',default_timeout=30).run()
        fresh.radio[0].set_value('离线规则').run()
        next(c for c in fresh.checkbox if c.label=='在本机保存宠物档案和会话').check().run()
        assert len(fresh.session_state['chat_session'].memory.pets)==1
        assert not next(iter(fresh.session_state['chat_session'].memory.pets.values())).facts
        next(c for c in app.checkbox if c.label=='确认永久删除本机已保存的全部会话和档案').check().run()
        next(b for b in app.button if b.label=='清除本机记忆').click().run()
        assert not app.exception
        assert not app.session_state['remember_local']
        assert MemoryStore(path).list_sessions()==[] and MemoryStore(path).profiles()==[]
