#!/usr/bin/env python3
"""HGLOS ReviewGate 병행 경로 브리지 (doc/34). Python 표준 라이브러리만 쓴다.

외부 프로그램 ReviewGate CLI를 셸 없이 인자 목록으로 한 번 부르고, stdout JSON 원문과 그 JSON이
절대 경로로 가리키는 파일만 읽어 --run-dir에 기록한다. 모델 호출·비용·허용 규칙은 ReviewGate와 사람이 관리한다.
이 스크립트는 허용 규칙을 만들거나 넓히지 않고, 결과 브랜치를 받아들이지 않으며, 재시도하지 않는다.

  review    --run-dir D --repo R --instruction F --reviewer SPEC [--reviewer SPEC] --input REL...
            [--reference REL...] --profile code|document --implementer-model M [--diff-base REV] [--timeout S]
  implement --run-dir D --repo R --instruction F --worker SPEC --reviewer SPEC... [--input REL...]
            [--reference REL...] [--check CMD...] [--base REV | --branch B] [--timeout S]
  discard   --run-dir D --repo R --branch B --confirmed-by "<사람 발화 인용>"
  status    --run-dir D

SPEC = <제공자> 또는 <제공자>=<전체 모델 ID>. 실행 파일은 환경 변수 REVIEWGATE_CLI(절대 경로)만 본다.
stdout: JSON 객체 하나(classification, exit, round ...). 종료 코드: 0 ok, 1 실패류, 2 거절·계약, 3 승인 필요·작업
폴더 변경, 4 ReviewGate 사용 불가. 분류·신원 판정·정식 회차 규칙은 doc/34 6.1·6.2.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cross_host  # noqa: E402  같은 폴더의 공용 실행기. 고치지 않고 재사용한다.

CLI_VENDOR = {'codex-cli': 'openai', 'claude-cli': 'anthropic'}
API_VENDOR = {'openai': 'openai', 'together': 'together'}
ON_HOLD = {'agy-cli', 'google'}
TIMEOUT = {'review': 1800, 'implement': 7200, 'discard': 600, 'status': 600, 'export': 600}
DEFAULT_CHECK = '로그 없음(요청 모델 default)'
NO_LOG = '로그 없음'
EXPORT_NAME = 'hglos.findings.live.json'
OK_STATUS = {'review': {'completed'}, 'implement': {'completed', 'no_changes'}, 'discard': {'discarded'},
             'status': {'ok'}}
EXIT = {'ok': 0, 'timeout': 1, 'failed': 1, 'review_partial': 1, 'partial': 1, 'checks_failed': 1,
        'reviewgate_busy': 1, 'needs_reconciliation': 1, 'contract': 2, 'input_too_large': 2,
        'egress_refused': 2, 'model_required': 2, 'provider_on_hold': 2, 'input_required': 2,
        'same_vendor': 2, 'confirmation_required': 2, 'usage': 2, 'approval_required': 3,
        'checkout_changed': 3, 'reviewgate_unavailable': 4}
RECORD_PATTERNS = ('request_{n}.md', 'reviewgate_{n}.stdout.json', 'reviewgate_{n}.stderr.txt',
                   'export_{n}.stdout.json', 'export_{n}.stderr.txt', 'call_{n}.json', 'reviewgate_{n}.raw.json',
                   'round_{n}.json', 'round_{n}.md')


class Stop(Exception):
    """호출 전 거절 또는 계약 위반으로 멈춘다."""

    def __init__(self, classification, message, round_no=None):
        super().__init__(message)
        self.classification = classification
        self.round_no = round_no


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Stop('usage', message)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_safe(node, path='', changed=None):
    """UTF-8 JSON으로 쓸 수 있게 값을 고친 사본과, 고친 필드 경로 목록을 돌려준다.

    고립 surrogate(U+D800~U+DFFF)는 '\\udXXX' 텍스트로, JSON 형식이 아닌 값은 '<타입 이름>: 값' 문자열로 바꾼다.
    고칠 것이 없으면 원래와 같은 값을 돌려준다(정상 기록의 바이트는 바뀌지 않는다).
    """
    changed = [] if changed is None else changed

    def fix_text(text, where):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
            changed.append(where or '$')
            return ''.join(f'\\u{ord(ch):04x}' if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)
        return text

    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            where = f'{path}.{key}' if path else str(key)
            safe_key = fix_text(key, where + '(key)') if isinstance(key, str) else str(key)
            out[safe_key] = json_safe(value, where, changed)[0]
        return out, changed
    if isinstance(node, (list, tuple)):
        return [json_safe(v, f'{path}[{i}]', changed)[0] for i, v in enumerate(node)], changed
    if isinstance(node, str):
        return fix_text(node, path), changed
    if node is None or isinstance(node, (bool, int, float)):
        return node, changed
    changed.append(path or '$')
    return f'<{type(node).__name__}>: {node!r}', changed


def emit(result):
    # 로캘(Windows cp949 등)과 상관없이 UTF-8 바이트로 쓴다. 쓸 수 없는 문자는 json_safe로 먼저 고친다.
    safe = json_safe(result)[0]
    try:
        sys.stdout.flush()
        sys.stdout.buffer.write((json.dumps(safe, ensure_ascii=False) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()
    except OSError:
        pass  # stdout이 닫혔으면 응답을 쓸 수 없다. 종료 코드는 그대로 돌려준다.
    return result['exit']


# ------------------------------------------------------------------ 제공자·전송 선언

def parse_spec(spec):
    provider, sep, model = spec.partition('=')
    return provider.strip(), (model.strip() or None) if sep else None


def requested_label(spec):
    provider, model = parse_spec(spec)
    return model if model else f'default:{provider}'


def vendor_of(provider):
    return CLI_VENDOR.get(provider) or API_VENDOR.get(provider)


def check_specs(specs):
    for spec in specs:
        provider, model = parse_spec(spec)
        if provider in ON_HOLD:
            raise Stop('provider_on_hold', f'{provider} is on hold (J3)')
    for spec in specs:
        provider, model = parse_spec(spec)
        if provider in API_VENDOR and not model:
            raise Stop('model_required', f'API provider {provider} needs <provider>=<model id>')


def expand(repo, rel):
    path = repo / rel
    if path.is_dir():
        listed = cross_host.git(repo, 'ls-files', '--', rel).decode('utf-8').splitlines()
        return [p for p in listed if p]
    return [Path(rel).as_posix()]


def check_egress(repo, specs, paths):
    try:
        declaration = cross_host.load_egress(repo)
    except cross_host.Refused as exc:
        raise Stop('egress_refused', str(exc))
    vendors = declaration.get('vendors') or []
    api_vendors = declaration.get('api_vendors')
    if api_vendors is not None and not (isinstance(api_vendors, list) and
                                        all(isinstance(v, str) and v.strip() for v in api_vendors)):
        raise Stop('egress_refused', 'api_vendors must be a list of non-empty strings')
    for spec in specs:
        provider, _ = parse_spec(spec)
        vendor = vendor_of(provider)
        if vendor is None:
            raise Stop('egress_refused', f'unknown provider {provider!r}')
        if vendor not in vendors:
            raise Stop('egress_refused', f'vendor {vendor} ({provider}) is not declared')
        if provider in API_VENDOR and vendor not in (api_vendors or []):
            raise Stop('egress_refused', f'API vendor {vendor} is not in api_vendors')
    globs = declaration.get('exclude_globs', [])
    for rel in paths:
        for item in expand(repo, rel):
            if cross_host.excluded(item, globs):
                raise Stop('egress_refused', f'{item} matches exclude_globs')


# ------------------------------------------------------------------ run 폴더

def next_round(rundir):
    numbers = [int(m.group(1)) for m in (re.fullmatch(r'call_(\d+)\.json', p.name) for p in rundir.glob('call_*.json'))
               if m]
    return max(numbers, default=0) + 1


def previous_records(rundir, n):
    records = []
    for path in rundir.glob('call_*.json'):
        m = re.fullmatch(r'call_(\d+)\.json', path.name)
        if not m or int(m.group(1)) >= n:
            continue
        try:
            records.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            records.append({'command': 'review', 'formal_round': None, 'unreadable': path.name})
    return sorted(records, key=lambda r: r.get('round') or 0)


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def snapshot(repo, rundir):
    """S7: 호출 전후 대상 저장소 상태. run 폴더가 저장소 안이면 뺀다."""
    exclude = []
    if inside(rundir, repo):
        exclude = [f':(exclude){Path(rundir).resolve().relative_to(repo).as_posix()}']
    try:
        head = cross_host.git(repo, 'rev-parse', '-q', '--verify', 'HEAD').decode().strip()
    except cross_host.Refused:
        head = ''
    status = cross_host.git(repo, 'status', '--porcelain=v1', '-uall', '-z', '--', '.', *exclude)
    entries = []
    for item in status.decode('utf-8', 'replace').split('\0'):
        if len(item) < 4:
            continue
        target = repo / item[3:]
        entries.append(f'{item[:2]} {item[3:]} {sha256(target.read_bytes()) if target.is_file() else "absent"}')
    index = cross_host.git(repo, 'ls-files', '-s', '-z', '--', '.', *exclude)
    return {'head': head, 'entries': len(entries), 'sha256': sha256('\n'.join(entries).encode('utf-8')),
            'index_sha256': sha256(index)}


def build_request(instruction, repo, diff_base, inputs):
    data = instruction
    if diff_base:
        text = data.decode('utf-8')
        if not text.endswith('\n'):
            text += '\n'
        diff = cross_host.git(repo, 'diff', '--no-color', '--no-ext-diff', diff_base, '--', *inputs) \
            .decode('utf-8', 'replace')
        untracked = [p for p in cross_host.git(repo, 'ls-files', '--others', '--exclude-standard', '--', *inputs)
                     .decode('utf-8').splitlines() if p]
        text += f'\n## 범위 diff (git diff {diff_base} -- 입력)\n```diff\n{diff}```\n'
        text += '\n## 미추적 입력 파일 (본문은 --input으로 보낸다)\n' + \
            ''.join(f'- {p}\n' for p in untracked or ['(없음)'])
        data = text.encode('utf-8')
    return data


# ------------------------------------------------------------------ 분류 (6.1)

def parse_stdout(out):
    try:
        data = json.loads(out.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def classify(command, code, timed_out, data):
    """(분류, 사유)."""
    if timed_out:
        return 'timeout', None
    if code == 11:
        return 'reviewgate_unavailable', (data or {}).get('error_code') or 'exit_11'
    if data is None or not isinstance(data.get('status'), str):
        return 'contract', 'stdout is not a JSON object with status'
    status, error = data.get('status'), data.get('error_code')
    details = data.get('details') if isinstance(data.get('details'), dict) else {}
    review = details.get('review') if isinstance(details.get('review'), dict) else {}
    if code == 10 or review.get('status') == 'approval_required':
        return 'approval_required', None
    if command == 'implement' and details.get('original_checkout_unchanged') is False:
        return 'checkout_changed', None
    if code == 13:
        return ('reviewgate_busy' if error == 'state_store_busy' else 'needs_reconciliation'), error
    if code in (2, 12) and error in ('instruction_limit', 'request_context_exceeded'):
        return 'input_too_large', error
    if code == 2:
        return 'contract', error
    if command == 'review' and code == 20 and status == 'partial' and error == 'partial_review':
        return 'review_partial', error
    if code != 0:
        return 'failed', error
    if command == 'implement' and status == 'completed':
        worker = details.get('worker_result') if isinstance(details.get('worker_result'), dict) else {}
        answer = worker.get('answer') if isinstance(worker.get('answer'), dict) else {}
        if review.get('status') != 'completed' or worker.get('status') != 'completed' or \
                ('status' in answer and answer.get('status') != 'done') or worker.get('skipped'):
            return 'partial', None
    if command == 'implement' and any(isinstance(c, dict) and c.get('passed') is False
                                      for c in details.get('checks') or []):
        return 'checks_failed', None
    if status in OK_STATUS[command]:
        return 'ok', None
    return 'contract', f'exit 0 with unknown status {status!r}'


# ------------------------------------------------------------------ 신원 (6.2)

def judge(spec, mi):
    """doc/34 6.2 attempt 판정. 기준은 브리지가 넘긴 SPEC이다(S2)."""
    provider, model = parse_spec(spec) if spec else (None, None)
    if spec is None:
        return NO_LOG
    if model is None:
        return DEFAULT_CHECK
    if not isinstance(mi, dict) or not mi.get('source') or not mi.get('model_observed'):
        return NO_LOG
    if mi.get('model_requested') != model or mi.get('cli_default_model') is True:
        return '불일치'
    final = mi.get('model_final')
    if provider == 'claude-cli':
        if not final:
            return '최종 작성자 미확인'
        return '일치' if final == model else '불일치'
    observed = mi.get('model_observed')
    observed = observed if isinstance(observed, list) else [observed]
    return '일치' if final == model and all(o == model for o in observed) else '불일치'


def spec_for(attempt, specs):
    mi = attempt.get('model_identity') if isinstance(attempt.get('model_identity'), dict) else {}
    names = [n for n in (mi.get('provider'), attempt.get('reviewer')) if isinstance(n, str)]
    for spec in specs:
        provider, _ = parse_spec(spec)
        if any(n == provider or n.startswith(provider + '-') for n in names):
            return spec
    return specs[0] if len(specs) == 1 else None


def round_check(checks):
    if not checks:
        return NO_LOG
    if '불일치' in checks:
        return '불일치'
    if NO_LOG in checks:
        return NO_LOG
    if DEFAULT_CHECK in checks:
        return DEFAULT_CHECK
    if '최종 작성자 미확인' in checks:
        return '최종 작성자 미확인'
    return '일치'


def instruction_values(text):
    found = []
    for m in re.finditer(r'"instruction"\s*:\s*', text):
        try:
            found.append(json.JSONDecoder().raw_decode(text, m.end())[0])
        except ValueError:
            found.append(None)
    return found


def rollout_f3(mi, request_bytes):
    """D11: Codex rollout으로 새 맥락(F3)을 확인한다. 본문은 기록하지 않는다."""
    out = {'ok': False, 'reason': None, 'file': None, 'context_lines': [], 'injected_items': [],
           'sent_prompt_sha256_match': None, 'instruction_count': None, 'instruction_match': None}
    src = mi.get('source') if isinstance(mi, dict) and isinstance(mi.get('source'), dict) else {}
    out['file'] = src.get('file')
    path, session = src.get('path'), (mi or {}).get('session_id')
    if not path or not Path(path).is_file():
        out['reason'] = 'rollout 없음'
        return out
    path = Path(path)
    if not session or not path.name.endswith(f'-{session}.jsonl'):
        out['reason'] = '파일 이름과 session_id 다름'
        return out
    rows = []
    for i, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append((i, row))
    contexts = [(i, r['payload']) for i, r in rows if r.get('type') == 'turn_context' and isinstance(r.get('payload'), dict)]
    out['context_lines'] = [i for i, _ in contexts]
    if not contexts or any(p.get('model') != mi.get('model_final') for _, p in contexts):
        out['reason'] = 'turn_context 모델이 model_final과 다름'
        return out
    messages = [(i, r['payload']) for i, r in rows if r.get('type') == 'response_item'
                and isinstance(r.get('payload'), dict) and r['payload'].get('type') == 'message']
    first = next((k for k, (_, p) in enumerate(messages) if p.get('role') == 'assistant'), len(messages))
    before = messages[:first]
    users = [k for k, (_, p) in enumerate(before) if p.get('role') == 'user']
    if not users:
        out['reason'] = '실제 요청 없음'
        return out
    bad, memory = 0, False
    for i, p in before[:users[-1]]:
        text = cross_host.message_text(p)
        role = p.get('role')
        head = next((h for h in ('# AGENTS.md instructions', '<environment_context>') if text.startswith(h)), None)
        # 머리줄은 허용 형식의 고정 머리만 남긴다. 본문(첫 줄 나머지 포함)은 기록하지 않는다.
        out['injected_items'].append({'line': i, 'role': role, 'head': head if role == 'user' else None,
                                      'sha256': sha256(text.encode('utf-8'))})
        if role == 'developer':
            memory = memory or '## Memory' in text
        elif not (role == 'user' and head):
            bad += 1
    request_text = cross_host.message_text(before[users[-1]][1])
    out['sent_prompt_sha256_match'] = sha256(request_text.encode('utf-8')) == mi.get('sent_prompt_sha256')
    values = instruction_values(request_text)
    out['instruction_count'] = len(values)
    out['instruction_match'] = len(values) == 1 and isinstance(values[0], str) and \
        values[0].encode('utf-8') == request_bytes
    if bad:
        out['reason'] = f'허용되지 않은 주입 {bad}건'
    elif memory:
        out['reason'] = 'developer 메시지에 ## Memory'
    elif not out['sent_prompt_sha256_match']:
        out['reason'] = 'sent_prompt_sha256 불일치'
    elif not out['instruction_match']:
        out['reason'] = f'instruction 값 {len(values)}개 또는 request 바이트와 다름'
    else:
        out['ok'] = True
    return out


def summarize_attempts(attempts, specs, request_bytes):
    rows = []
    for a in attempts:
        if not isinstance(a, dict):
            rows.append({'model_identity': None, 'model_check': NO_LOG})
            continue
        mi = a.get('model_identity')
        spec = spec_for(a, specs)
        row = {k: a.get(k) for k in ('attempt_id', 'unit_id', 'reviewer', 'phase', 'error_code', 'fallback_used')}
        row.update(spec=spec, model_identity=mi, model_check=judge(spec, mi))
        if spec and parse_spec(spec)[0] == 'codex-cli':
            row['f3'] = rollout_f3(mi, request_bytes)
        rows.append(row)
    return rows


def source_text(provider, attempts):
    mi = next((a.get('model_identity') for a in attempts if isinstance(a.get('model_identity'), dict)), {}) or {}
    src = mi.get('source') if isinstance(mi.get('source'), dict) else {}
    if provider == 'codex-cli':
        lines = ','.join(map(str, src.get('lines') or [src.get('line')]))
        return f"rollout {src.get('file') or '출처 없음'}:{lines} turn_context.payload.model, 관측 출처: CLI 로컬 기록"
    if provider == 'claude-cli':
        return (f"ReviewGate model_identity, stdout:{src.get('line')} assistant.message.model, stream 미보존, "
                '관측 출처: CLI 로컬 기록')
    return 'ReviewGate model_identity, response_body.model, 관측 출처: API 응답 본문'


def header(record, specs, implementer, version):
    attempts = record['attempts']
    check = record['model_check']
    observed = record.get('model_observed') or '없음'
    provider = parse_spec(specs[0])[0] if len(specs) == 1 else '+'.join(parse_spec(s)[0] for s in specs)
    if check == '일치':
        # cross_host.header와 같은 규칙: 순위 숫자는 정식 회차(record['rank'])에만 적는다. 정식이 아니면 '제외'다.
        _, status = cross_host.rank_status(observed, implementer)
        rank = record.get('rank') if record.get('formal_round') else None
        rank_text = str(rank) if rank is not None else ('미확인(등급 미확인)' if status == 'unknown_grade' else '제외')
        same = '예' if cross_host.family(observed) == cross_host.family(implementer) else '아니오'
    else:
        models = [parse_spec(s)[1] for s in specs]
        req_rank = cross_host.rank_of(models[0], implementer) if len(models) == 1 and models[0] else None
        rank_text = f"미확인({check} · 요청 기준 {req_rank if req_rank else '제외'})"
        same = '미확인'
    tail = '' if record.get('formal_round') else ' · 정식 회차 아님'
    return (f"검증자: ReviewGate review({version or '버전 미확인'}) · 요청 모델 {', '.join(record['reviewers_requested'])}"
            f" · 관측 모델 {observed}({source_text(provider, attempts)}, attempt {len(attempts)}개) · 판정: {check}"
            f" · 순위: {rank_text} · 세션: 새 세션 · 구현자와 같은 패밀리: {same}{tail}")


# ------------------------------------------------------------------ export와 회차 (6.2, S5)

def export_round(record, exe, rundir, n, timeout):
    argv = ['export', '--run', str(record.get('run_id')), '--format', 'hglos-findings']
    code, out, err, timed_out = cross_host.run_process([exe, *argv], cwd=str(rundir), timeout=timeout)
    cross_host.write_new(rundir / f'export_{n}.stdout.json', out or b'')
    cross_host.write_new(rundir / f'export_{n}.stderr.txt', err or b'')
    data = None if timed_out else parse_stdout(out or b'')
    info = {'argv': ['$REVIEWGATE_CLI', *argv], 'exit_code': code, 'timed_out': timed_out,
            'status': (data or {}).get('status'), 'error_code': (data or {}).get('error_code'),
            'stdout_sha256': sha256(out or b'')}
    record['export'] = info
    if timed_out:
        return 'contract', 'contract:export timeout', None
    if code in (13, 14, 20):
        return None, f"export_incomplete:{info['error_code'] or code}", None
    if code != 0:
        return 'contract', f'contract:export exit {code}', None
    if data is None:
        return 'contract', 'contract:export stdout is not a JSON object', None
    items = [a for a in data.get('artifacts') or [] if isinstance(a, dict)
             and re.split(r'[\\/]', str(a.get('path') or ''))[-1] == EXPORT_NAME]
    if len(items) != 1:
        return 'contract', f'contract:export artifact {EXPORT_NAME} found {len(items)} times', None
    item = items[0]
    info['artifact'] = {k: item.get(k) for k in ('path', 'absolute_path', 'sha256', 'bytes', 'kind')}
    if not item.get('absolute_path') or not Path(item['absolute_path']).is_absolute() \
            or not Path(item['absolute_path']).is_file():
        return 'contract', 'contract:export absolute_path missing', None
    payload = Path(item['absolute_path']).read_bytes()
    if sha256(payload) != item.get('sha256') or len(payload) != item.get('bytes'):
        return 'contract', 'contract:export sha256 or bytes mismatch', None
    return None, None, payload


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _K32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _K32.CreateFileW.restype = wintypes.HANDLE
    _K32.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                               ctypes.c_void_p)
    _K32.WriteFile.restype = wintypes.BOOL
    _K32.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _K32.SetFileInformationByHandle.restype = wintypes.BOOL
    _K32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _K32.CloseHandle.restype = wintypes.BOOL
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_WRITE, _DELETE, _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL = 0x40000000, 0x00010000, 1, 0x80
    _FILE_DISPOSITION_INFO = 4  # FILE_INFO_BY_HANDLE_CLASS.FileDispositionInfo
    _ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS = 80, 183


class Published:
    """정식 경로에 새로 만든 파일 하나. 게시가 모두 끝날 때까지 핸들을 쥔다.

    Windows: CreateFileW(CREATE_NEW, 공유 0, DELETE 권한). 핸들을 쥔 동안 다른 핸들은 열 수 없으므로
    실패 시 이 핸들의 삭제 표시(FileDispositionInfo)로 지우면 닫는 순간 반드시 사라진다.
    POSIX: O_CREAT|O_EXCL로 만든다. 열린 파일도 경로에서 unlink할 수 있으므로 실패 시 unlink로 지운다.
    """

    def __init__(self, path, handle):
        self.path, self.handle = path, handle

    def close(self):
        if self.handle is None:
            return
        if os.name == 'nt':
            _K32.CloseHandle(self.handle)
        else:
            os.close(self.handle)
        self.handle = None

    def discard(self):
        """이번 호출이 만든 이 파일을 지운다. 실패하면 오류 문자열을 돌려준다."""
        error = None
        if os.name == 'nt':
            flag = ctypes.c_ubyte(1)
            if not _K32.SetFileInformationByHandle(self.handle, _FILE_DISPOSITION_INFO, ctypes.byref(flag), 1):
                error = f'{self.path.name}: delete disposition failed winerror={ctypes.get_last_error()}'
            self.close()
        else:
            try:
                os.unlink(self.path)
            except OSError as exc:
                error = f'{self.path.name}: {exc}'
            self.close()
        return error


def _write_all(handle, data):
    if os.name == 'nt':
        view = memoryview(data)
        while view:
            done = wintypes.DWORD(0)
            chunk = view[:1 << 20]
            buf = (ctypes.c_char * len(chunk)).from_buffer_copy(chunk)
            if not _K32.WriteFile(handle, buf, len(chunk), ctypes.byref(done), None) or done.value == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            view = view[done.value:]
    else:
        view = memoryview(data)
        while view:
            view = view[os.write(handle, view):]


def publish_file(src, dst):
    """임시 위치의 파일을 정식 경로에 새로 만든다. 기존 파일은 덮어쓰지 않는다(FileExistsError).

    돌려준 Published의 핸들은 호출한 쪽이 게시를 모두 마친 뒤 close()하거나, 실패 시 discard()한다.
    """
    data = src.read_bytes()
    if os.name == 'nt':
        handle = _K32.CreateFileW(str(dst), _GENERIC_WRITE | _DELETE, 0, None, _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
        if handle in (None, _INVALID_HANDLE):
            code = ctypes.get_last_error()
            if code in (_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS):
                raise FileExistsError(17, 'File exists', str(dst))
            raise ctypes.WinError(code)
    else:
        handle = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o644)
    item = Published(dst, handle)
    try:
        _write_all(handle, data)
    except BaseException as exc:
        # 쓰기 중 어떤 예외(MemoryError, KeyboardInterrupt 포함)든 이 파일은 이번 호출이 만든 것이다.
        # 핸들로 지우고 닫은 뒤, 삭제 결과를 예외에 붙여 다시 올린다(make_formal이 사유에 남긴다).
        exc.hglos_discard_error = _safe_discard(item)
        raise
    return item


def _safe_discard(item):
    """Published.discard()를 부르고, discard 자체가 예외를 내도 대상 파일 이름과 함께 오류 문자열로 돌려준다."""
    try:
        return item.discard()
    except BaseException as exc:  # noqa: BLE001 - 롤백 경로는 끝까지 간다
        try:
            item.close()
        except BaseException:  # noqa: BLE001
            pass
        return f'{item.path.name}: discard raised {type(exc).__name__}: {exc}'


def _rollback(published):
    """이번 호출이 게시 중인 파일을 모두 지운다. 지우지 못한 것은 '<파일>: <오류>' 목록으로 돌려준다."""
    return [e for e in (_safe_discard(item) for item in published) if e]


def _remove_owned_stage(stage_root, rundir):
    """이번 호출이 만든 stage만 지운다. 지우지 못하면 남은 위치와 오류를 돌려준다."""
    errors = []

    def collect(func, path, exc):
        exc = exc[1] if isinstance(exc, tuple) else exc
        try:
            where = Path(path).relative_to(rundir).as_posix()
        except ValueError:
            where = str(path)
        errors.append(f'{where}: {type(exc).__name__}: {exc}')

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(stage_root, onexc=collect)
        else:
            shutil.rmtree(stage_root, onerror=collect)
        left = stage_root.exists()
        remaining = sorted(p.relative_to(rundir).as_posix() for p in stage_root.rglob('*')) if left else []
    except Exception as exc:  # noqa: BLE001 - 정리 실패도 기록으로 남긴다
        errors.append(f'{stage_root.name}: {type(exc).__name__}: {exc}')
        left, remaining = stage_root.exists(), []
    return {'stage': stage_root.relative_to(rundir).as_posix(), 'removed': not left, 'left': remaining,
            'errors': errors}


def make_formal(record, rundir, n, payload, hold=None):
    """export 사본 검증과 회차 변환은 run 폴더 안 이번 호출 전용 임시 위치에서 한다.

    validate_findings와 make_round가 성공하고 contract_violations가 비었을 때만 raw·round 파일을 정식 경로에 게시한다.
    게시는 Published 핸들을 모두 쥔 채 진행하고, 하나라도 실패하면 이번 호출이 만든 파일을 핸들로 지운다.
    hold가 주어지면 게시에 성공한 핸들을 닫지 않고 hold에 넘긴다. 호출한 쪽(run)이 call_N.json 기록을 확정한 뒤에
    닫고, 확정에 실패하면 롤백한다(정식 파일과 call_N.json 확정 사이의 경쟁, V-close4).
    임시 위치는 호출마다 새 이름이며, 이번 호출이 만든 것만 지우고 그 결과를 record['formal_stage_cleanup']에 남긴다.
    """
    try:
        cross_host.verify_script('validate_findings.py')
        cross_host.verify_script('make_round_json.py')
        cross_host.schema_path()
    except cross_host.Refused:
        return None, 'round_tools_missing'
    names = (f'reviewgate_{n}.raw.json', f'round_{n}.json', f'round_{n}.md')
    taken = [name for name in names if (rundir / name).exists()]
    if taken:
        return 'contract', f'contract:formal record already exists: {", ".join(taken)}'
    # make_round는 출력 폴더를 cwd 기준 상대 경로로 받고 그 값을 round JSON의 report_path에 남긴다.
    # 임시 폴더 안에 run 폴더와 같은 이름의 하위 폴더를 두어 report_path가 바로 게시한 경우와 같게 한다.
    stage_root = rundir / f'.formal-stage-{n}-{os.getpid()}-{os.urandom(4).hex()}'
    try:
        stage_root.mkdir()
    except Exception as exc:  # noqa: BLE001
        # 만들지 못한 폴더는 이번 호출의 것이 아니므로 지우지 않는다.
        return 'contract', f'contract:formal stage not available: {type(exc).__name__}: {exc}'
    stage = stage_root / rundir.name
    published = []
    step = 'stage'
    try:
        try:
            stage.mkdir()
            step = 'stage raw'
            raw = stage / names[0]
            cross_host.write_new(raw, payload)
            step = 'validate_findings'
            ok, message = cross_host.validate_findings(raw)
            if not ok:
                return 'contract', f'contract:validate_findings: {message[:300]}'
            step = 'make_round'
            path, violations = cross_host.make_round(stage_root, stage, raw, n)
            if path is None or violations:
                return 'contract', f"contract:{'; '.join(violations)[:300]}"
            pairs = [(raw, rundir / names[0]), (path, rundir / names[1])]
            if (stage / names[2]).is_file():
                pairs.append((stage / names[2], rundir / names[2]))
            step = 'publish'
            for src, dst in pairs:
                try:
                    published.append(publish_file(src, dst))
                except Exception as exc:  # noqa: BLE001 - MemoryError 등도 계약 실패로 기록한다
                    # publish_file은 자기가 만든 파일을 이미 지웠다(hglos_discard_error). 다른 프로세스가 먼저 만든
                    # 파일(FileExistsError)은 publish_file이 만들지 않았으므로 건드리지 않는다.
                    errors = [e for e in [getattr(exc, 'hglos_discard_error', None)] if e] + _rollback(published)
                    published = []
                    what = str(exc) if isinstance(exc, OSError) else f'{type(exc).__name__}: {exc}'
                    return 'contract', f'contract:publish failed at {dst.name}: {what}' + \
                        (f' (rollback errors: {"; ".join(errors)})' if errors else '')
            if hold is None:
                for item in published:
                    item.close()
            else:
                hold.extend(published)
            published = []
            record['round_json'] = names[1]
            return None, None
        except Exception as exc:  # noqa: BLE001 - stage·변환 중 예상 밖 예외도 call_N.json에 남긴다
            errors = _rollback(published)
            published = []
            return 'contract', f'contract:formal conversion failed at {step}: {type(exc).__name__}: {exc}' + \
                (f' (rollback errors: {"; ".join(errors)})' if errors else '')
    finally:
        # KeyboardInterrupt·SystemExit처럼 Exception이 아닌 예외는 여기서 정리만 하고 다시 올라간다.
        _rollback(published)
        record['formal_stage_cleanup'] = _remove_owned_stage(stage_root, rundir)


def flag_reasons(raw_attempts):
    """D9·6.2: 모든 attempt의 fallback_used(attempt·model_identity)와 resumed가 JSON false(`is False`)여야 한다.

    누락, null, 0, "false", true는 모두 요건 미충족이다. 호출 분류는 바꾸지 않고 정식 회차 이유로만 남긴다.
    """
    checks = (('fallback', 'attempt.fallback_used', lambda a, mi: a, 'fallback_used'),
              ('fallback', 'model_identity.fallback_used', lambda a, mi: mi, 'fallback_used'),
              ('resumed', 'model_identity.resumed', lambda a, mi: mi, 'resumed'))
    reasons = []
    for code, label, pick, key in checks:
        bad = []
        for i, a in enumerate(raw_attempts):
            a = a if isinstance(a, dict) else {}
            mi = a.get('model_identity') if isinstance(a.get('model_identity'), dict) else {}
            holder = pick(a, mi)
            if holder.get(key) is not False or key not in holder:
                bad.append((i, 'missing' if key not in holder else json.dumps(holder[key], ensure_ascii=False)))
        if bad:
            i, value = bad[0]
            reasons.append(f'{code}:{label} is not false in {len(bad)} attempt(s), first attempts[{i}]={value}')
    return reasons


def formal_review(record, args, specs, rundir, n, exe, request_bytes, details, hold=None):
    """정식 회차 조건. 후보(검수자 1개 + 전체 ID + code)만 본다."""
    reasons = []
    candidate = len(specs) == 1 and parse_spec(specs[0])[1] is not None and args.profile == 'code'
    record['formal_candidate'] = candidate
    if not candidate:
        return ['not_candidate'], None
    classification = record['classification']
    if classification != 'ok':
        reasons.append(f'not_ok:{classification}')
    check = record['model_check']
    if check != '일치':
        reasons.append(f'model_check:{check}')
    else:
        rank, status = cross_host.rank_status(record['model_observed'], args.implementer_model)
        record['rank_status'] = status
        if status == 'excluded':
            reasons.append('rank_excluded')
    previous = [r for r in previous_records(rundir, n) if r.get('command') == 'review']
    if any(r.get('formal_round') is not True for r in previous):
        reasons.append('run_stopped')
    elif previous and previous[0].get('model_observed') != record['model_observed']:
        reasons.append('model_changed')
    sessions = [(a.get('model_identity') or {}).get('session_id') for a in record['attempts']]
    used = {(a.get('model_identity') or {}).get('session_id') for r in previous for a in r.get('attempts') or []}
    if not sessions or not all(sessions) or len(set(sessions)) != len(sessions) or set(sessions) & used:
        reasons.append('session_id')
    if parse_spec(specs[0])[0] == 'codex-cli' and not all((a.get('f3') or {}).get('ok') for a in record['attempts']):
        reasons.append('f3')
    raw_attempts = details.get('attempts') if isinstance(details.get('attempts'), list) else []
    reasons.extend(flag_reasons(raw_attempts))
    digest = sha256(request_bytes)
    if details.get('request_sha256') != digest or \
            any((a.get('model_identity') or {}).get('request_sha256') != digest for a in record['attempts']):
        reasons.append('request_sha256')
    if record['integrity']['changed']:
        reasons.append('integrity')
    if classification != 'ok':
        return reasons, None
    # 후보이고 분류가 ok면 export를 부른다(U4 실측). 정식 변환은 다른 조건이 모두 맞을 때만 한다.
    new_class, reason, payload = export_round(record, exe, rundir, n, TIMEOUT['export'])
    if reason:
        reasons.append(reason)
    if new_class or reasons:
        return reasons, new_class
    new_class, reason = make_formal(record, rundir, n, payload, hold)
    if reason:
        reasons.append(reason)
    return reasons, new_class


# ------------------------------------------------------------------ 명령

def parser():
    p = Parser(prog='reviewgate_bridge.py', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='command', required=True, parser_class=Parser)
    rv = sub.add_parser('review')
    im = sub.add_parser('implement')
    for s in (rv, im):
        s.add_argument('--repo', required=True)
        s.add_argument('--instruction', required=True)
        s.add_argument('--reviewer', action='append', default=[])
        s.add_argument('--input', action='append', default=[])
        s.add_argument('--reference', action='append', default=[])
        s.add_argument('--timeout', type=float)
    rv.add_argument('--profile', choices=('code', 'document'), required=True)
    rv.add_argument('--implementer-model', required=True)
    rv.add_argument('--diff-base')
    im.add_argument('--worker', required=True)
    im.add_argument('--check', action='append', default=[])
    group = im.add_mutually_exclusive_group()
    group.add_argument('--base')
    group.add_argument('--branch')
    dc = sub.add_parser('discard')
    dc.add_argument('--repo', required=True)
    dc.add_argument('--branch', required=True)
    dc.add_argument('--confirmed-by')
    dc.add_argument('--timeout', type=float)
    st = sub.add_parser('status')
    st.add_argument('--timeout', type=float)
    for s in (rv, im, dc, st):
        s.add_argument('--run-dir', required=True)
    return p


def precheck(args):
    """호출 전 거절. run 폴더에 아무것도 쓰지 않는다."""
    if args.command == 'discard':
        if not (args.confirmed_by or '').strip():
            raise Stop('confirmation_required', 'discard needs --confirmed-by "<human quote>"')
        return
    if args.command == 'status':
        return
    repo = Path(args.repo).resolve()
    specs = list(args.reviewer) + ([args.worker] if args.command == 'implement' else [])
    check_specs(specs)
    if not args.reviewer:
        raise Stop('input_required', 'at least one --reviewer is required')
    if args.command == 'review' and not args.input:
        raise Stop('input_required', 'review needs at least one --input')
    if args.command == 'implement':
        worker = parse_spec(args.worker)[0]
        if worker in API_VENDOR and not args.input:
            raise Stop('input_required', 'API worker needs at least one --input')
        if any(vendor_of(parse_spec(r)[0]) == vendor_of(worker) for r in args.reviewer):
            raise Stop('same_vendor', 'reviewer vendor equals worker vendor')
    check_egress(repo, specs, list(args.input) + list(args.reference))


def rg_argv(args, repo, request):
    if args.command == 'review':
        argv = ['review', '--prompt-file', str(request), '--source-root', str(repo), '--repository-root', str(repo)]
        for r in args.reviewer:
            argv += ['--reviewer', r]
        for i in args.input:
            argv += ['--input', i]
        for r in args.reference:
            argv += ['--reference', r]
        return argv + ['--profile', args.profile, '--no-fallback']
    if args.command == 'implement':
        argv = ['implement', '--prompt-file', str(request), '--repo', str(repo), '--worker', args.worker]
        for r in args.reviewer:
            argv += ['--reviewer', r]
        for i in args.input:
            argv += ['--input', i]
        for r in args.reference:
            argv += ['--reference', r]
        for c in args.check:
            argv += ['--check', c]
        if args.base:
            argv += ['--base', args.base]
        if args.branch:
            argv += ['--branch', args.branch]
        return argv + ['--no-fallback']
    if args.command == 'discard':
        return ['discard', '--repo', str(repo), '--branch', args.branch]
    return ['policy', 'status']


def copy_details(record, data):
    details = data.get('details') if isinstance(data.get('details'), dict) else {}
    review = details.get('review') if isinstance(details.get('review'), dict) else {}
    worker = details.get('worker_result') if isinstance(details.get('worker_result'), dict) else {}
    record.update(run_id=data.get('run_id'), cost=data.get('cost'), report_path=details.get('report_path'),
                  diff_path=details.get('diff_path'), branch=details.get('branch'), commit=details.get('commit'),
                  changed_files=details.get('changed_files'), review_status=review.get('status'),
                  original_checkout_unchanged=details.get('original_checkout_unchanged'))
    if isinstance(details.get('checks'), list):
        record['checks'] = [{k: c.get(k) for k in ('command', 'exit_code', 'passed', 'timed_out', 'log_path')}
                            for c in details['checks'] if isinstance(c, dict)]
    if worker:
        record['worker_cost'] = {k: worker.get(k) for k in ('cost_usd', 'cost_basis', 'reservation_usd')}
    violations = details.get('violations') if 'violations' in details else review.get('violations')
    if violations is not None:
        record['violations'] = violations
    return details, review, worker


def run(args):
    precheck(args)
    rundir = Path(args.run_dir).resolve()
    rundir.mkdir(parents=True, exist_ok=True)
    n = next_round(rundir)
    taken = [p.format(n=n) for p in RECORD_PATTERNS if (rundir / p.format(n=n)).exists()]
    if taken:
        raise Stop('contract', f'record files already exist: {", ".join(taken)}', n)
    repo = Path(args.repo).resolve() if getattr(args, 'repo', None) else None
    record = {'command': args.command, 'round': n, 'started_at': now(), 'argv': None,
              'cwd': str(repo) if repo else '<run-dir>'}
    held = []  # 게시에 성공한 정식 파일의 Published 핸들. call_N.json 기록이 확정될 때까지 쥔다.
    step = {'name': 'record'}
    try:
        try:
            _run_call(args, record, rundir, n, repo, held, step)
        except Exception as exc:  # noqa: BLE001 - 기록이 생긴 뒤의 예외는 모두 contract로 기록한다(V-close5)
            fail_record(args, record, held, f'bridge error at {step["name"]}: {type(exc).__name__}: {exc}',
                        error={'step': step['name'], 'type': type(exc).__name__, 'message': str(exc)[:500]})
        return commit_record(args, record, rundir, n, held)
    finally:
        # KeyboardInterrupt·SystemExit처럼 Exception이 아닌 예외는 여기서 이번 호출의 정식 파일만 지우고 다시 올라간다.
        _rollback(held)


def _run_call(args, record, rundir, n, repo, held, step):
    """ReviewGate 호출과 판정으로 record를 채운다. call_N.json은 쓰지 않는다(commit_record가 쓴다)."""
    if args.command in ('review', 'implement'):
        record['reviewers_requested'] = [requested_label(r) for r in args.reviewer]
    if args.command == 'review':
        record['implementer_model'] = args.implementer_model
    if args.command == 'implement':
        record['worker_requested'] = requested_label(args.worker)
        record['implementer_model'] = record['worker_requested']
    if args.command == 'discard':
        record['confirmed_by'] = args.confirmed_by
    exe = os.environ.get('REVIEWGATE_CLI')  # D5: 이 변수 하나만 본다. PATH는 보지 않는다.
    if not exe or not Path(exe).is_absolute() or not Path(exe).is_file():
        record.update(classification='reviewgate_unavailable', reason='not_installed', exit=EXIT['reviewgate_unavailable'],
                      ended_at=now(), formal_round=False)
        return
    request = None
    request_bytes = b''
    if args.command in ('review', 'implement'):
        step['name'] = 'request'
        request_bytes = build_request(Path(args.instruction).read_bytes(), repo,
                                      getattr(args, 'diff_base', None), list(args.input))
        request = rundir / f'request_{n}.md'
        cross_host.write_new(request, request_bytes)
        record['request_sha256'] = sha256(request_bytes)
    argv = rg_argv(args, repo, request)
    record['argv'] = ['$REVIEWGATE_CLI', *argv]
    timeout = args.timeout or TIMEOUT[args.command]
    record['timeout_seconds'] = timeout
    step['name'] = 'integrity before'
    before = snapshot(repo, rundir) if args.command == 'review' else None
    step['name'] = 'reviewgate call'
    code, out, err, timed_out = cross_host.run_process([exe, *argv], cwd=str(repo or rundir), timeout=timeout)
    record.update(ended_at=now(), exit_code=code, timed_out=timed_out, stdout_sha256=sha256(out or b''))
    step['name'] = 'reviewgate output records'
    cross_host.write_new(rundir / f'reviewgate_{n}.stdout.json', out or b'')
    cross_host.write_new(rundir / f'reviewgate_{n}.stderr.txt', err or b'')
    if before is not None:
        step['name'] = 'integrity after'
        after = snapshot(repo, rundir)
        record['integrity'] = {'before': before, 'after': after, 'changed': before != after}
    step['name'] = 'classify'
    data = None if timed_out else parse_stdout(out or b'')
    classification, reason = classify(args.command, code, timed_out, data)
    record.update(status=(data or {}).get('status'), error_code=(data or {}).get('error_code'),
                  classification=classification, reason=reason)
    if timed_out:
        record['note'] = 'ReviewGate가 남긴 worktree·비용 예약은 브리지가 정리하지 않는다. 사람에게 알린다.'
    step['name'] = 'copy details'
    details = copy_details(record, data)[0] if data else {}
    if args.command == 'review':
        record['formal_round'] = False
        if data is not None:
            step['name'] = 'attempts'
            attempts = details.get('attempts') if isinstance(details.get('attempts'), list) else []
            record['attempts'] = summarize_attempts(attempts, list(args.reviewer), request_bytes)
            record['model_check'] = round_check([a['model_check'] for a in record['attempts']])
            finals = [(a.get('model_identity') or {}).get('model_final') for a in record['attempts']]
            record['model_observed'] = parse_spec(args.reviewer[0])[1] if record['model_check'] == '일치' \
                else next((f for f in finals if f), None)
            step['name'] = 'formal review'
            reasons, new_class = formal_review(record, args, list(args.reviewer), rundir, n, exe, request_bytes, details,
                                               held)
            record['formal_reasons'] = reasons
            record['formal_round'] = not reasons and bool(record.get('round_json'))
            if new_class:
                record['classification'] = new_class
            record['rank'] = cross_host.rank_of(record['model_observed'], args.implementer_model) \
                if record['formal_round'] else None
            step['name'] = 'header'
            record['header'] = build_header(args, record)
        else:
            record['formal_reasons'] = [f"not_ok:{classification}"]
    if args.command == 'implement' and data is not None:
        step['name'] = 'implement identity'
        worker = details.get('worker_result') if isinstance(details.get('worker_result'), dict) else {}
        record['worker_identity'] = worker.get('model_identity')
        record['worker_model_check'] = judge(args.worker, worker.get('model_identity'))
        review = details.get('review') if isinstance(details.get('review'), dict) else {}
        attempts = review.get('attempts') if isinstance(review.get('attempts'), list) else []
        record['attempts'] = summarize_attempts(attempts, list(args.reviewer), request_bytes)
        record['model_check'] = round_check([a['model_check'] for a in record['attempts']])
        record['formal_round'] = False
        record['formal_reasons'] = ['not_candidate']  # implement 교차 검수는 늘 참고 검수다(6.2)
    step['name'] = 'record'


def build_header(args, record):
    attempts = record.get('attempts') or []
    version = next(((a.get('model_identity') or {}).get('cli_version') for a in attempts
                    if isinstance(a, dict) and (a.get('model_identity') or {}).get('cli_version')), None)
    return header(record, list(args.reviewer), args.implementer_model, version)


def fail_record(args, record, held, message, error=None):
    """이번 호출을 contract로 바꾼다. 게시 중인 정식 파일을 지우고, 머리줄을 비정식으로 다시 만든다."""
    errors = _rollback(held)
    held.clear()
    if 'call_classification' not in record:
        record['call_classification'] = record.get('classification')
    record['classification'] = 'contract'
    record['exit'] = EXIT['contract']
    record['formal_round'] = False
    record.pop('round_json', None)
    record['rank'] = None
    reasons = record.get('formal_reasons')
    record['formal_reasons'] = (list(reasons) if isinstance(reasons, list) else []) + \
        [f'contract:{message}' + (f' (rollback errors: {"; ".join(errors)})' if errors else '')]
    if error:
        record['bridge_error'] = error
    if 'header' in record:
        try:
            record['header'] = build_header(args, record)
        except Exception:  # noqa: BLE001 - 머리줄을 만들 수 없으면 비운다
            record['header'] = None


def encode_record(record):
    return (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def write_call_record(path, data):
    """call_N.json을 새로 만든다. 이미 있으면 FileExistsError(남의 파일이므로 건드리지 않는다).

    쓰는 도중 실패하면 이번 호출이 만든 부분 파일을 지우고 예외를 다시 올린다. 0바이트·부분 기록을 남기지 않는다.
    """
    handle = open(path, 'xb')
    try:
        with handle:
            handle.write(data)
            handle.flush()
    except BaseException:
        try:
            handle.close()
        except BaseException:  # noqa: BLE001
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def commit_record(args, record, rundir, n, held):
    """record를 검사·인코딩해 call_N.json으로 확정하고, 그 뒤에만 정식 파일 핸들을 닫는다.

    - UTF-8 JSON으로 쓸 수 없는 값이 있으면 고친 사본을 쓰고 contract로 강등한다(정식 파일은 롤백).
    - 인코딩을 끝낸 뒤에만 파일을 만든다. 쓰기 실패 시 이번 호출이 만든 부분 파일은 지운다.
    - call_N.json을 남길 수 없으면(경쟁 작성자의 파일 등) 정식 파일을 롤백하고 stdout JSON(과 stderr)에만 남긴다.
    """
    if 'classification' not in record:
        fail_record(args, record, held, 'bridge error: no classification')
    record['exit'] = EXIT.get(record['classification'], EXIT['contract'])
    safe, changed = json_safe(record)
    if changed:
        fail_record(args, record, held, 'call record has values that UTF-8 JSON cannot hold: ' + ', '.join(changed[:10]) +
                    ' (lone surrogates written as \\uXXXX text)')
        record['record_sanitized_fields'] = changed
        safe = json_safe(record)[0]
    try:
        data = encode_record(safe)
    except Exception as exc:  # noqa: BLE001 - json_safe 뒤라 나오지 않아야 하지만, 나오면 최소 기록으로 남긴다
        fail_record(args, record, held, f'call record could not be encoded: {type(exc).__name__}: {exc}')
        safe = json_safe({k: record.get(k) for k in ('command', 'round', 'started_at', 'argv', 'cwd', 'classification',
                                                     'call_classification', 'exit', 'formal_round', 'formal_reasons',
                                                     'bridge_error', 'reason', 'status', 'error_code')})[0]
        data = encode_record(safe)
    path = rundir / f'call_{n}.json'
    try:
        write_call_record(path, data)
    except Exception as exc:  # noqa: BLE001
        errors = _rollback(held)
        held.clear()
        conflict = isinstance(exc, FileExistsError)
        reasons = list(safe.get('formal_reasons') or [])
        reasons.append(f'contract:call record not written: {exc}' +
                       (f' (rollback errors: {"; ".join(errors)})' if errors else ''))
        try:
            sys.stderr.write(f'reviewgate_bridge: call_{n}.json not written ({exc}); formal files of this call rolled back\n')
        except Exception:  # noqa: BLE001
            pass
        return emit({'classification': 'contract', 'exit': EXIT['contract'], 'round': n, 'record': None,
                     'reason': 'call_record_conflict' if conflict else 'call_record_write_failed',
                     'formal_round': False, 'formal_reasons': reasons,
                     'call_classification': safe.get('call_classification') or safe.get('classification'),
                     'rollback_errors': errors, 'branch': safe.get('branch'), 'header': None})
    for item in held:
        item.close()
    held.clear()
    return finish(safe, rundir, n)


def finish(record, rundir, n):
    return emit({'classification': record['classification'], 'exit': record['exit'], 'round': n,
                 'record': str(rundir / f'call_{n}.json'), 'reason': record.get('reason'),
                 'formal_round': record.get('formal_round'), 'formal_reasons': record.get('formal_reasons'),
                 'branch': record.get('branch'), 'header': record.get('header')})


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        return run(args)
    except Stop as stop:
        return emit({'classification': stop.classification, 'exit': EXIT[stop.classification],
                     'round': stop.round_no, 'record': None, 'message': str(stop)})


if __name__ == '__main__':
    sys.exit(main())
