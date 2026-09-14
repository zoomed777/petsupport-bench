"""Evaluate explicit multi-turn memory assertions, separately from frozen rubric scores."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.chat_session import ChatSession
from app.memory import units
from app.triage.pipeline import TriagePipeline
from eval.run_artifacts import (manifest, run_directory, read_records, append_record,
                                write_json, provider_settings)

MISSING = object()


def lookup(snapshot, path):
    value = snapshot
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def check(snapshot, expectation):
    actual = lookup(snapshot, expectation['path'])
    op, expected = expectation['op'], expectation.get('value')
    if op == 'absent':
        passed = actual is MISSING
    elif actual is MISSING:
        passed = False
    elif op == 'eq':
        passed = actual == expected
    elif op in {'contains', 'excludes'}:
        present = expected in actual if isinstance(actual, (list, str, dict)) else None
        passed = present is not None and (present if op == 'contains' else not present)
    elif op in {'lte', 'gte'}:
        passed = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if passed:
            passed = actual <= expected if op == 'lte' else actual >= expected
    else:
        raise ValueError('Unknown assertion operator')
    return {**expectation, 'actual': None if actual is MISSING else actual,
            'missing': actual is MISSING, 'passed': bool(passed)}


def snapshot(chat, reply):
    pets = {}
    for pet in chat.memory.pets.values():
        try:
            context = pet.context('继续')
        except ValueError:
            context = None
        pets[pet.name] = {'name': pet.name, 'profile': pet.known_profile(),
                          'facts': pet.facts.copy(), 'risks': list(pet.risks),
                          'context': context, 'context_units': units(context) if context else None,
                          'retained_turns': len(pet.recent), 'folded_turns': pet.folded_turns}
    # Copy now: later turns must not mutate earlier snapshots.
    return json.loads(json.dumps({'pets': pets,
        'active': pets.get(chat.memory.active.name) if chat.memory.active else None,
        'pet_count': len(chat.memory.pets), 'topic': chat.memory.topic,
        'content': reply['content'], 'report': bool(reply['report']),
        'intent': reply['audit']['intent'], 'pending_slots': [q['slot'] for q in chat.pending]},
        ensure_ascii=False))


def validate_suite(suite):
    cases = suite.get('cases', [])
    if not cases or len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('Empty suite or duplicate case ID')
    for case in cases:
        if not case.get('steps'):
            raise ValueError('Empty scenario')
        for step in case['steps']:
            if not isinstance(step.get('input'), str) or not step.get('checks'):
                raise ValueError('Each turn needs input and explicit checks')
            if step.get('action') not in {None, 'new_conversation'}:
                raise ValueError('Unsupported scenario action')
            for item in step['checks']:
                if not isinstance(item.get('path'), str) or item.get('op') not in {'eq', 'contains', 'excludes', 'absent', 'lte', 'gte'}:
                    raise ValueError('Invalid assertion')


class CallBudgetExceeded(RuntimeError):
    pass


def record_client(client, calls, budget, pause):
    original = client._complete

    def complete(system, user, temperature=0.0):
        if budget['remaining'] <= 0:
            raise CallBudgetExceeded('Invocation request budget exhausted')
        budget['remaining'] -= 1
        if pause:
            time.sleep(pause)
        record = {'system': system, 'user': user, 'temperature': temperature,
                  'timestamp': datetime.now(timezone.utc).isoformat()}
        before = len(client.calls)
        start = time.monotonic()
        try:
            record['output'] = original(system, user, temperature)
            record['status'] = 'ok'
            return record['output']
        except Exception as exc:
            record.update(status='error', error=type(exc).__name__)
            raise
        finally:
            record['latency_seconds'] = round(time.monotonic() - start, 3)
            record['provider'] = client.calls[-1] if len(client.calls) > before else None
            calls.append(record)
    client._complete = complete


def summarize(records, suite, mode, run_hash):
    latest = {}
    allowed = {case['case_id'] for case in suite['cases']}
    for row in records:
        if row['case_id'] not in allowed:
            raise ValueError('Unexpected scenario in cache')
        latest[row['case_id']] = row
    turns = [turn for row in latest.values() for turn in row['turns']]
    calls = [call for turn in turns for call in turn['calls']]
    checks = [item for turn in turns for item in turn['checks']]
    return {'run_hash': run_hash, 'mode': mode,
            'scope': 'author-constructed engineering assertions; not clinical or full response-quality evaluation',
            'expected_scenarios': len(suite['cases']),
            'expected_turns': sum(len(c['steps']) for c in suite['cases']),
            'completed_scenarios': sum(r['status'] == 'ok' for r in latest.values()),
            'passed_scenarios': sum(r['passed'] for r in latest.values()),
            'completed_turns': len(turns), 'passed_turns': sum(t['passed'] for t in turns),
            'checks': len(checks), 'passed_checks': sum(c['passed'] for c in checks),
            'provider_calls': len(calls), 'successful_provider_calls': sum(c['status'] == 'ok' for c in calls),
            'provider_errors': sum(c['status'] != 'ok' for c in calls),
            'historical_scenario_attempts': len(records),
            'scenarios': [{'case_id': c['case_id'], 'category': c['category'],
                           'status': latest.get(c['case_id'], {}).get('status', 'not_run'),
                           'passed': latest.get(c['case_id'], {}).get('passed', False),
                           'failed_checks': [{'turn': t['turn'], **item}
                                             for t in latest.get(c['case_id'], {}).get('turns', [])
                                             for item in t['checks'] if not item['passed']]}
                          for c in suite['cases']]}, latest


def run_suite(suite, out, mode, settings=None, pipeline_factory=None, max_calls=80, pause=1.35):
    validate_suite(suite)
    if mode not in {'offline', 'live'} or max_calls < 1:
        raise ValueError('Invalid mode or request budget')
    paths = [Path(__file__), ROOT / 'eval/run_artifacts.py', ROOT / 'app/chat_session.py',
             ROOT / 'app/memory.py', ROOT / 'app/memory_store.py', ROOT / 'requirements-demo.txt',
             ROOT / 'data/knowledge_base.jsonl', ROOT / 'config/redflag_rules.json']
    paths += sorted((ROOT / 'app/triage').glob('*.py'))
    paths += sorted((ROOT / 'app/guardrails').glob('*.py'))
    paths += sorted((ROOT / 'app/models').glob('*.py'))
    config = manifest(suite, {**(settings or {}), 'kind': 'multiturn-v1', 'mode': mode}, paths)
    factory = pipeline_factory or (TriagePipeline.from_environment if mode == 'live' else TriagePipeline)
    budget = {'remaining': max_calls}
    with run_directory(out, suite, config) as target:
        path = target / 'traces.jsonl'
        records = read_records(path, config['run_hash'])
        _, done = summarize(records, suite, mode, config['run_hash'])
        for case in suite['cases']:
            if done.get(case['case_id'], {}).get('status') == 'ok':
                continue  # Completed quality failures stay failures, not rerolled successes.
            chat, turns = ChatSession(), []
            status = 'ok'
            for number, step in enumerate(case['steps'], 1):
                if step.get('action') == 'new_conversation':
                    chat = chat.new_conversation()
                pipeline, calls, errors = None, [], []
                try:
                    pipeline = factory()
                    if mode == 'live':
                        if pipeline.client is None:
                            raise RuntimeError('Hy3 configuration missing')
                        record_client(pipeline.client, calls, budget, pause)
                    reply = chat.reply(step['input'], pipeline)
                    errors = list(reply['audit']['errors'])
                    if any(call['status'] != 'ok' for call in calls) and not errors:
                        errors.append('ProviderCallFailed')
                    observed = snapshot(chat, reply)
                    checks = [check(observed, item) for item in step['checks']]
                    passed = all(item['passed'] for item in checks) and not errors
                    turns.append({'turn': number, 'input': step['input'], 'action': step.get('action'),
                                  'checks': checks, 'snapshot': observed, 'reply': reply,
                                  'calls': calls, 'errors': errors, 'passed': passed})
                except Exception as exc:
                    errors = [type(exc).__name__]
                    turns.append({'turn': number, 'input': step['input'], 'checks': [],
                                  'calls': calls, 'errors': errors, 'passed': False})
                finally:
                    if pipeline and pipeline.client:
                        pipeline.client.client.close()
                print(case['case_id'], number, 'PASS' if turns[-1]['passed'] else 'FAIL', flush=True)
                if errors:
                    status = 'error'
                    break
            record = {'case_id': case['case_id'], 'category': case['category'], 'mode': mode,
                      'run_hash': config['run_hash'], 'status': status,
                      'timestamp': datetime.now(timezone.utc).isoformat(), 'turns': turns,
                      'passed': status == 'ok' and len(turns) == len(case['steps']) and all(t['passed'] for t in turns)}
            append_record(path, record)
            records.append(record)
            if status == 'error':
                break  # Preserve partial results; do not burn quota through an outage.
        summary, _ = summarize(records, suite, mode, config['run_hash'])
        write_json(target / 'summary.json', summary)
        return summary


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['offline', 'live'], default='offline')
    parser.add_argument('--cases', default='data/multiturn_eval_v1.json')
    parser.add_argument('--out', default=None)
    parser.add_argument('--max-calls', type=int, default=80, help='Upper bound on SDK calls in this invocation; SDK may retry once')
    args = parser.parse_args()
    suite = json.loads((ROOT / args.cases).read_text(encoding='utf-8'))
    settings = provider_settings() if args.mode == 'live' else {}
    try:
        result = run_suite(suite, ROOT / (args.out or f'results/multiturn_v1_{args.mode}'),
                           args.mode, settings, max_calls=args.max_calls)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps({k: v for k, v in result.items() if k != 'scenarios'}, ensure_ascii=False))
    if result['passed_scenarios'] != result['expected_scenarios'] or (args.mode == 'live' and not result['provider_calls']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
