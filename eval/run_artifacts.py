"""Versioned, exclusive experiment directories; legacy evidence is read-only."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def manifest(inputs, settings, paths):
    files = {str(Path(p).resolve().relative_to(ROOT)).replace('\\', '/'):
             hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    payload = {'schema': 1, 'inputs_sha256': digest(inputs),
               'settings': settings, 'code_sha256': files}
    return {**payload, 'run_hash': digest(payload)}


def write_json(path, value):
    """Replace only derived summaries; traces and input snapshots use other paths."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


@contextmanager
def run_directory(out, inputs, expected):
    """Reject mismatches BEFORE writes. A lock prevents two writers sharing a run."""
    out = Path(out).resolve()
    for frozen in (ROOT / 'results/final', ROOT / 'results/memory'):
        if out == frozen or frozen in out.parents:
            raise ValueError('Frozen results are read-only; choose a new --out directory')
    if out.exists() and any(out.iterdir()) and not (out / 'manifest.json').exists():
        raise ValueError('Legacy/nonempty directory has no manifest; choose a new --out directory')
    out.mkdir(parents=True, exist_ok=True)
    lock = out / '.run.lock'
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError:
        raise ValueError('Run directory is locked; use another directory or check the existing process') from None
    try:
        with handle:
            handle.write(str(os.getpid()))
        path = out / 'manifest.json'
        snapshot = out / 'inputs.json'
        if path.exists():
            current = json.loads(path.read_text(encoding='utf-8'))
            if current != expected:
                raise ValueError('Run fingerprint changed; choose a new --out directory')
            if not snapshot.exists() or digest(json.loads(snapshot.read_text(encoding='utf-8'))) != expected['inputs_sha256']:
                raise ValueError('Input snapshot missing or changed; refusing cached results')
        else:
            # Exclusive creation, never overwrite someone else's inputs.
            with snapshot.open('x', encoding='utf-8') as f:
                f.write(json.dumps(inputs, ensure_ascii=False, indent=2) + '\n')
            with path.open('x', encoding='utf-8') as f:
                f.write(json.dumps(expected, ensure_ascii=False, indent=2) + '\n')
        yield out
    finally:
        lock.unlink(missing_ok=True)


def read_records(path, run_hash):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f'Incomplete trace at line {line_number}; preserve it and use a new directory') from None
        if row.get('run_hash') != run_hash:
            raise ValueError('Trace fingerprint mismatch; refusing cached results')
        rows.append(row)
    return rows


def append_record(path, row):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def provider_settings():
    """Fingerprint endpoint without publishing URLs that might contain credentials."""
    import platform
    from importlib.metadata import version
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    endpoint = os.getenv('HY3_BASE_URL') or os.getenv('SUPPORT_AGENT_LLM_BASE_URL', '')
    return {'model': os.getenv('HY3_MODEL') or os.getenv('SUPPORT_AGENT_LLM_REPLY_MODEL', 'hy3'),
            'endpoint_sha256': hashlib.sha256(endpoint.encode()).hexdigest(),
            'temperature': 0, 'max_tokens': 4096, 'thinking': 'disabled',
            'context_budget_units': os.getenv('HY3_CONTEXT_BUDGET_UNITS', '24000'),
            'timeout_seconds': os.getenv('HY3_TIMEOUT_SECONDS', '60'),
            'sdk_retries': 1, 'python': platform.python_version(),
            'openai': version('openai'), 'httpx': version('httpx')}
