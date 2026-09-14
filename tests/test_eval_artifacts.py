"""No-network regressions for versioned caches and the multi-turn checker."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from eval.run_artifacts import (ROOT, manifest, run_directory, read_records, append_record,
                                write_json)
from scripts.validate_final import run_validation, summarize as summarize_validation
from scripts.eval_multiturn import check, snapshot, validate_suite
from app.chat_session import ChatSession
from app.triage.pipeline import TriagePipeline


def config(inputs, **settings):
    return manifest(inputs, settings, [ROOT / 'eval/run_artifacts.py'])


def files(out):
    return {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}


def test_same_manifest_resumes_without_rewriting_inputs(tmp_path):
    inputs = [{'id': 'one', 'text': 'original'}]
    with run_directory(tmp_path, inputs, config(inputs)):
        pass
    before = files(tmp_path)
    with run_directory(tmp_path, inputs, config(inputs)):
        pass
    assert files(tmp_path) == before


@pytest.mark.parametrize('change', ['input', 'model', 'code'])
def test_changed_fingerprint_rejected_before_writes(tmp_path, change):
    inputs = [{'id': 'one', 'text': 'original'}]
    original = config(inputs, model='hy3')
    with run_directory(tmp_path, inputs, original):
        pass
    before = files(tmp_path)
    modified_inputs, modified = deepcopy(inputs), deepcopy(original)
    if change == 'input':
        modified_inputs[0]['text'] = 'changed but same ID'
        modified = config(modified_inputs, model='hy3')
    elif change == 'model':
        modified = config(inputs, model='another')
    else:
        modified['code_sha256']['eval/run_artifacts.py'] = 'different'
    with pytest.raises(ValueError, match='fingerprint'):
        with run_directory(tmp_path, modified_inputs, modified):
            pytest.fail('should reject before entering')
    assert files(tmp_path) == before


def test_legacy_results_never_adopted_or_overwritten(tmp_path):
    write_json(tmp_path / 'validation_inputs.jsonl', {'old': True})
    before = files(tmp_path)
    with pytest.raises(ValueError, match='Legacy'):
        with run_directory(tmp_path, [], config([])):
            pass
    assert files(tmp_path) == before


@pytest.mark.parametrize('folder', ['results/final', 'results/final/nested', 'results/memory'])
def test_frozen_directories_rejected(folder):
    with pytest.raises(ValueError, match='Frozen'):
        with run_directory(ROOT / folder, [], config([])):
            pass


def test_snapshot_tampering_is_detected(tmp_path):
    with run_directory(tmp_path, [], config([])):
        pass
    write_json(tmp_path / 'inputs.json', ['changed'])
    before = files(tmp_path)
    with pytest.raises(ValueError, match='snapshot'):
        with run_directory(tmp_path, [], config([])):
            pass
    assert files(tmp_path) == before


def test_run_lock_blocks_concurrent_writer(tmp_path):
    with run_directory(tmp_path, [], config([])):
        with pytest.raises(ValueError, match='locked'):
            with run_directory(tmp_path, [], config([])):
                pass
    assert not (tmp_path / '.run.lock').exists()


def test_trace_fingerprint_and_partial_line_are_rejected(tmp_path):
    path = tmp_path / 'traces.jsonl'
    append_record(path, {'run_hash': 'old'})
    with pytest.raises(ValueError, match='fingerprint'):
        read_records(path, 'new')
    with path.open('a', encoding='utf-8') as f:
        f.write('{')
    with pytest.raises(ValueError, match='Incomplete'):
        read_records(path, 'old')


def test_failed_judgement_retried_successful_cache_reused(tmp_path):
    calls = []
    class Client:
        def __init__(self):
            self.calls = []
        def _complete(self, *_):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError()
            self.calls.append({'id': 'unit-test-only'})
            return json.dumps({'scores': [4]*7, 'evidence': ['unit test']*7, 'gate': False})
    rows = [{'case': {'case_id': 'one', 'user_message': 'test'}, 'tier': 'good', 'output': 'test'}]
    first = run_validation(tmp_path, rows, 2, Client, {'test': True}, pause=0)
    assert first['completed'] == 1 and len(calls) == 2
    second = run_validation(tmp_path, rows, 2, Client, {'test': True}, pause=0)
    assert second['completed'] == 2 and len(calls) == 3
    third = run_validation(tmp_path, rows, 2, Client, {'test': True}, pause=0)
    assert third == second and len(calls) == 3
    assert third['failed_attempts'] == 1 and third['attempts'] == 3
    before = files(tmp_path)
    rows[0]['output'] = 'new candidate under same ID'
    with pytest.raises(ValueError, match='fingerprint'):
        run_validation(tmp_path, rows, 2, Client, {'test': True}, pause=0)
    assert len(calls) == 3 and files(tmp_path) == before


def test_partial_repeats_do_not_count_as_ranked_scenario():
    rows = [{'case': {'case_id': 'one'}, 'tier': tier} for tier in ['good', 'medium', 'bad']]
    records = [{'case_id': 'one', 'tier': tier, 'repeat': 0, 'status': 'ok', 'verdict': {'total': score}}
               for tier, score in [('good', 90), ('medium', 50), ('bad', 10)]]
    summary, _ = summarize_validation(records, rows, 3, 'test')
    assert summary['ranked_scenarios'] == 0 and summary['mean_repeat_sd'] is None


def test_missing_field_does_not_pass_excludes():
    assert not check({}, {'path': 'missing', 'op': 'excludes', 'value': 'old'})['passed']
    assert check({}, {'path': 'missing', 'op': 'absent'})['passed']


SUITE = json.loads((ROOT / 'data/multiturn_eval_v1.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', SUITE['cases'], ids=lambda c: c['case_id'])
def test_multiturn_constructed_regressions(case):
    validate_suite({'cases': [case]})
    chat = ChatSession()
    snapshots = []
    for step in case['steps']:
        if step.get('action') == 'new_conversation':
            chat = chat.new_conversation()
        reply = chat.reply(step['input'], TriagePipeline())
        observed = snapshot(chat, reply)
        snapshots.append(json.dumps(observed, ensure_ascii=False))
        failures = [check(observed, item) for item in step['checks'] if not check(observed, item)['passed']]
        assert not failures, failures
    assert snapshots  # All scenarios have actually executed.


def test_snapshot_is_not_mutated_by_later_turns():
    chat = ChatSession()
    original = snapshot(chat, chat.reply('狗叫豆豆，3岁，今天呕吐', TriagePipeline()))
    encoded = json.dumps(original, ensure_ascii=False)
    chat.reply('说错了，不是呕吐，是拉稀', TriagePipeline())
    assert json.dumps(original, ensure_ascii=False) == encoded


def test_reply_audit_is_a_historical_snapshot():
    chat = ChatSession()
    original = chat.reply('狗叫豆豆，3岁，今天呕吐', TriagePipeline())
    encoded = json.dumps(original, ensure_ascii=False)
    chat.reply('说错了，不是呕吐，是拉稀', TriagePipeline())
    assert json.dumps(original, ensure_ascii=False) == encoded


def test_duplicate_name_requires_a_distinct_label():
    chat = ChatSession()
    chat.reply('狗叫豆豆，3岁，今天食欲不好', TriagePipeline())
    reply = chat.reply('另一只狗叫豆豆，1岁，今天拉稀', TriagePipeline())
    assert '不同的称呼' in reply['content']
    assert len(chat.memory.pets) == 1 and chat.answers['age'] == '3岁'


def test_multiturn_cache_keeps_completed_quality_failures(tmp_path):
    from scripts.eval_multiturn import run_suite
    suite = {'cases': [{'case_id': 'bad-expectation', 'category': 'unit-test', 'steps': [
        {'input': '狗叫豆豆，3岁，今天食欲不好',
         'checks': [{'path': 'active.profile.age', 'op': 'eq', 'value': '99岁'}]}]}]}
    first = run_suite(suite, tmp_path, 'offline', pause=0)
    assert first['completed_scenarios'] == 1 and first['passed_scenarios'] == 0
    before = files(tmp_path)
    def should_not_run():
        pytest.fail('Completed quality failures must not be rerolled')
    second = run_suite(suite, tmp_path, 'offline', pipeline_factory=should_not_run, pause=0)
    assert first == second and before == files(tmp_path)


def test_live_without_model_stops_and_is_not_passed(tmp_path):
    from scripts.eval_multiturn import run_suite
    result = run_suite({'cases': SUITE['cases'][:2]}, tmp_path, 'live',
                       pipeline_factory=TriagePipeline, pause=0)
    assert result['passed_scenarios'] == 0 and result['completed_scenarios'] == 0
    assert result['historical_scenario_attempts'] == 1 and result['provider_calls'] == 0


def test_provider_recorder_enforces_budget_and_preserves_raw_output():
    from scripts.eval_multiturn import record_client, CallBudgetExceeded
    class Client:
        def __init__(self):
            self.calls = []
        def _complete(self, *_):
            self.calls.append({'id': 'unit-test-id'})
            return 'raw test output'
    client, calls = Client(), []
    record_client(client, calls, {'remaining': 1}, pause=0)
    assert client._complete('system', 'user') == 'raw test output'
    with pytest.raises(CallBudgetExceeded):
        client._complete('system', 'user')
    assert len(client.calls) == len(calls) == 1
    assert calls[0]['output'] == 'raw test output' and calls[0]['provider']['id'] == 'unit-test-id'
