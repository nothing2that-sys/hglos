#!/usr/bin/env python3
"""다른 회사 에이전트 CLI(codex exec, claude -p, agy -p)를 읽기 전용 보조 실행기로 부른다 (doc/33).

    python cross_host.py call  --repo R --run RUN --round N --tool codex|claude|agy --kind review|context
                               --instruction FILE [--scope REL]... [--canon REL]... [--implementer-model M]
                               [--model ID] [--effort E] [--timeout SEC] [--downgraded-from RUN]
    python cross_host.py usage [--tools codex,agy,claude] [--claude-usage-note TEXT]
    python cross_host.py route --usage FILE --implementer-model M --main codex|claude --kind review|context
    python cross_host.py clean --run RUN

결과는 <repo>/validation/<RUN>/ 의 회차별 파일로 남긴다(request_<N>.md, call_<N>.json,
usage-before/after_<N>.json, <tool>_<N>.*, integrity_<N>.json, 정식 회차만 round_<N>.json).
저장소 밖에는 tempfile.gettempdir()/hglos-cross-host/<RUN>/ 한 폴더에만 쓴다(agy 패킷·호출별 로그).
이미 있는 회차 파일은 덮어쓰지 않는다. CLI는 셸 없이 인자 목록으로 부르고, 시간 초과 시
프로세스 트리째 끝낸다. 재시도·자동 로그인·모델 fallback은 하지 않는다. --scope는 필수다.

한 run은 한 도구와 한 검토 대상(HEAD·범위·정본·제외 glob·diff·범위 파일 내용의 지문 target_sha256)만 다룬다.
대상이 바뀌거나, 신원 판정 실패(identity_unconfirmed)나 호출 중 저장소 변경(integrity) 뒤에는 새 run을 쓴다.
CLI를 찾지 못하거나 시작하지 못하면 contract 실패로 call_<N>.json에 남긴다.

모델 신원은 CLI가 스스로 남긴 로컬 기록에서만 읽는다(Codex rollout, Claude stream-json,
agy --log-file). 순위는 신원 판정이 `일치`일 때만 매긴다. 계약 상세는 tests/test_cross_host.py
모듈 docstring(동결된 oracle)이다. 검증자 순위표(doc/35 0.3, doc/33 6.3 갱신)의 계약은
tests/test_verifier_rank.py(동결된 oracle)에도 있다.

종료 코드: 0 = 성공, 2 = 호출 전 거절(input_too_large 포함)·입력 오류, 1 = 호출 뒤 실패.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
TOOLS = ('codex', 'claude', 'agy')
VENDOR = {'codex': 'openai', 'claude': 'anthropic', 'agy': 'google'}
DEFAULT_MODEL = {'codex': 'gpt-6.1-sol', 'claude': 'claude-opus-5-5'}
DEFAULT_EFFORT = 'medium'
TIMEOUT = {'review': 900, 'context': 600}
AGY_PRINT_MARGIN = 60
REQUEST_LIMIT = 400000
PROMPT_LIMIT = 8000
PACKET_LIMIT = 2000000
THRESHOLD_FRACTION = 0.10
THRESHOLD_USED_PERCENT = 90
API_KEY_ENV = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY')
SECRET_KEYS = {'accountId', 'installationId', 'account_id', 'installation_id'}
AGY_GROUPS = {'flash': 'Gemini Models', 'argon': 'Gemini 4 Argon', 'third_party': 'Claude and GPT models'}
AGY_FLASH, AGY_ARGON, AGY_CLAUDE = 'gemini-3.8-flash-low', 'gemini-4-argon-low', 'claude-opus-5-5-high'
CLAUDE_TOOLS = 'Read,Grep,Glob'
# 구현자 모델별로 사람이 2순위로 확인한 같은 회사 다른 모델(비슷하거나 더 큰 등급. 구현자보다 한 단계 이상
# 작은 모델은 넣지 않는다). 이름·버전으로 추정하지 않는다. 목록 밖이면 Anthropic 구현자는 제외,
# OpenAI 구현자는 순위 미확인이다. 근거: doc/35 0.2(D2), doc/02 J5.
SIMILAR_GRADE = {'gpt-6.1-sol': (), 'claude-opus-5-5': ('claude-fable-5-2',)}
RUN_PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
RESERVED_PART = re.compile(r'agy-packet-\d+|agy-schema-\d+\.json|canary', re.I)
FAILURE_MARKERS = (
    ('auth', ('Failed to authenticate', 'OAuth session expired', 'not logged into Antigravity',
              'authentication failed or timed out', 'Not logged in')),
    ('limit', ('usage limit', 'rate limit', 'quota exceeded', 'error_max_budget_usd', 'at capacity')),
    ('sandbox_network', ('no such host', 'Access is denied', 'ENOTFOUND', 'getaddrinfo')),
)
# 6.2 경로표. 구현자 Claude·메인 Codex 행은 HOST.md "네이티브 우선"으로 native를 먼저 둔다(U13 D1).
REVIEW_ROUTES = {
    ('openai', 'codex'): ['claude-p', 'agy-claude', 'native-codex', 'handoff'],
    ('anthropic', 'claude'): ['codex-exec', 'native-claude', 'handoff'],
    ('anthropic', 'codex'): ['native-codex', 'codex-exec', 'handoff'],
    ('openai', 'claude'): ['native-claude', 'claude-p', 'handoff'],
}
CONTEXT_ROUTES = {
    'codex': ['agy-gemini', 'claude-p', 'native-codex'],
    'claude': ['agy-gemini', 'codex-exec', 'native-claude'],
}


class Refused(Exception):
    """호출 전 거절. failure가 있으면 call_<N>.json에 기록한다."""

    def __init__(self, message, failure=None):
        super().__init__(message)
        self.failure = failure


# ------------------------------------------------------------------ 공통

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def find_asset(*candidates):
    for rel in candidates:
        path = (HERE / rel).resolve()
        if path.is_file():
            return path
    raise Refused(f'missing bundled file: {candidates[0]}')


def schema_path():
    return find_asset('../skills/hglos-verify/assets/findings-schema.json', '../assets/findings-schema.json')


def verify_script(name):
    return find_asset(f'../skills/hglos-verify/scripts/{name}', name)


def tool_cmd(tool):
    raw = os.environ.get(f'HGLOS_CROSS_HOST_{tool.upper()}_CMD')
    if raw:
        cmd = json.loads(raw)
        if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)):
            raise Refused(f'HGLOS_CROSS_HOST_{tool.upper()}_CMD must be a JSON string list')
        return cmd
    found = shutil.which(tool)
    if not found:
        raise Refused(f'{tool} not found on PATH')
    return [found]


def child_env():
    return {k: v for k, v in os.environ.items() if k.upper() not in API_KEY_ENV}


def windows_descendants(root_pid, since):
    """root_pid의 자손을 [(pid, 핸들)]로 돌려준다(부모 PID 계보). 부모가 이미 끝났어도 자식의 ParentProcessId는
    남으므로 찾을 수 있다. 호출자가 핸들로 끝내고 닫는다(terminate_handles).

    PID 재사용으로 무관한 프로세스를 고르지 않도록:
    - since(FILETIME 정수, run 시작) 이후, 자기 부모 이후, 스냅샷 이전에 생긴 프로세스만 고른다. 스냅샷 뒤 PID를
      다시 쓴 새 프로세스는 스냅샷보다 늦게 생겼으므로 빠진다.
    - 생성 시각을 확인한 핸들을 끝낼 때까지 쥔다. 핸들이 열려 있는 동안 그 PID는 재사용되지 않으므로, 확인한
      바로 그 프로세스만 끝낸다.
    """
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    parents = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
    k32.CloseHandle(snap)
    until = windows_now()  # 스냅샷에 든 프로세스는 이 시각 전에 생겼다
    handles = {}

    def created(pid):
        handle = handles.get(pid)
        if handle is None:
            handle = k32.OpenProcess(0x1001, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
            if not handle:
                return None
            handles[pid] = handle
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(wintypes.HANDLE(handle), *[ctypes.byref(t) for t in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    found = []
    try:
        found = select_descendants(parents, created, root_pid, since, until)
    finally:
        for pid, handle in list(handles.items()):
            if pid not in found:
                k32.CloseHandle(handle)
    return [(pid, handles[pid]) for pid in found]


def terminate_handles(pairs):
    """windows_descendants가 쥔 핸들로 그 프로세스만 끝내고 핸들을 닫는다."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    for _, handle in pairs:
        try:
            k32.TerminateProcess(handle, 1)
        finally:
            k32.CloseHandle(handle)


def select_descendants(parents, created, root_pid, since, until=None):
    """parents {pid: 부모 pid}, created(pid) -> 생성 시각 또는 None. 계보를 따라 자손을 고른다.

    자식은 run 시작(since)뿐 아니라 자기 부모보다도 뒤에 생겼어야 한다. 끝난 프로세스의 PID를 자손이 다시
    쓰면, 그 PID를 부모로 적은 옛 자식(부모보다 먼저 생김)이 남아 있어도 계보에 넣지 않는다.
    until(스냅샷 시각)이 있으면 그 뒤에 생긴 프로세스(스냅샷 뒤 PID를 다시 쓴 새 프로세스)도 넣지 않는다.
    """
    root_birth = created(root_pid)
    found, frontier = [], [(root_pid, max(since, root_birth) if root_birth is not None else since)]
    while frontier:
        parent, parent_birth = frontier.pop()
        for pid, ppid in parents.items():
            if ppid == parent and pid not in found and pid != root_pid:
                birth = created(pid)
                if birth is not None and birth >= parent_birth and (until is None or birth <= until):
                    found.append(pid)
                    frontier.append((pid, birth))
    return found


def windows_now():
    import ctypes
    from ctypes import wintypes
    now = wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
    return (now.dwHighDateTime << 32) | now.dwLowDateTime


def kill_tree(proc, job=None, since=None):
    if os.name == 'nt':
        if job:
            job.terminate()
        if since is None:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
            return
        # Job이 없거나 늦게 할당돼 빠진 자손, 부모가 먼저 끝난 자손을 계보로 찾아 확인한 핸들로 끝낸다.
        # root는 Popen이 핸들을 쥐고 있어 PID가 재사용되지 않는다. 그 사이 새로 생긴 자손이 있을 수 있어 몇 번 반복한다.
        try:
            proc.kill()
        except OSError:
            pass
        try:
            for _ in range(3):
                pairs = windows_descendants(proc.pid, since)
                if not pairs:
                    break
                terminate_handles(pairs)
        except (OSError, AttributeError, ValueError):
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


class WindowsJob:
    """Windows Job Object. 부모가 먼저 끝난 자손까지 시간 초과 때 끝낸다.

    KILL_ON_JOB_CLOSE는 쓰지 않는다: 정상 종료 뒤에는 아무것도 끝내지 않고, 시간 초과 때만 terminate한다.
    생성·할당에 실패하면 조용히 부모 PID 계보만 쓴다(available=False). 그때 중간 부모가 먼저 끝난
    손자는 계보가 끊겨 찾지 못할 수 있다(잔존 위험, Job을 쓸 수 없는 경우에만).
    """

    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes
        self.k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.k32.OpenProcess.restype = wintypes.HANDLE
        self.k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self.k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = self.k32.CreateJobObjectW(None, None)
        self.available = False
        if not self.handle:
            return
        process = self.k32.OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if process:
            self.available = bool(self.k32.AssignProcessToJobObject(self.handle, process))
            self.k32.CloseHandle(process)

    def terminate(self):
        if self.handle and self.available:
            self.k32.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


CREATE_SUSPENDED = 0x00000004


def resume_process(pid):
    """CREATE_SUSPENDED로 시작한 프로세스를 재개한다(ntdll NtResumeProcess). 성공하면 True."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        ntdll = ctypes.WinDLL('ntdll')
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        handle = k32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
        if not handle:
            return False
        try:
            return ntdll.NtResumeProcess(wintypes.HANDLE(handle)) == 0
        finally:
            k32.CloseHandle(handle)
    except (OSError, AttributeError):
        return False


def run_process(argv, *, cwd=None, stdin=None, timeout, soft_deadline=None):
    """셸 없이 실행한다. 시간 초과면 트리째 끝내고 timed_out=True.

    soft_deadline: CLI 자체 시간 제한(agy --print-timeout). 프로세스가 0이 아닌 코드로 스스로 끝났고 그 시간에
    이르렀으면 시간 초과로 끝난 것이므로 남은 자손을 Job/계보째 정리한다. 반환 값(timed_out)은 실행기 제한만 뜻한다.
    """
    # Windows는 멈춘 상태로 시작해 Job에 넣은 뒤 재개한다. 그래서 Job을 쓸 수 있으면 모든 자손이 Job 안에서 생긴다.
    kwargs = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED} if os.name == 'nt'
              else {'start_new_session': True})
    # 시계는 프로세스 시작 전에 잰다. Job 설정이 늦어도 CLI 자체 제한(soft_deadline) 판정이 밀리지 않게 한다.
    started = time.monotonic()
    since = windows_now() if os.name == 'nt' else None
    proc = subprocess.Popen(argv, cwd=cwd, env=child_env(), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    job = None
    if os.name == 'nt':
        try:
            job = WindowsJob(proc.pid)
        except (OSError, AttributeError):
            job = None
        if not resume_process(proc.pid):
            # 재개하지 못하면 멈춘 채로 남기지 않는다.
            proc.kill()
            proc.communicate()
            if job:
                job.close()
            raise OSError('suspended process could not be resumed')
    try:
        out, err = proc.communicate(stdin, timeout=max(timeout - (time.monotonic() - started), 0.1))
        # 자체 제한으로 끝난 경우만 정리한다. 제한 안에 정상 종료(0)했으면 경과 시간이 길어도 건드리지 않는다.
        if soft_deadline is not None and proc.returncode != 0 and time.monotonic() - started >= soft_deadline:
            kill_tree(proc, job, since)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        kill_tree(proc, job, since)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = b'', b''
        return None, out or b'', err or b'', True
    finally:
        if job:
            job.close()


def tool_version(tool):
    try:
        code, out, _, timed_out = run_process(tool_cmd(tool) + ['--version'], timeout=60)
    except (OSError, Refused):
        return None
    text = out.decode('utf-8', 'replace').strip()
    return text.splitlines()[0] if code == 0 and text else None


def scrub(node):
    """계정·설치 ID 키를 재귀로 지운다."""
    if isinstance(node, dict):
        return {k: scrub(v) for k, v in node.items() if k not in SECRET_KEYS}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


def scrub_text(text):
    """저장용 사본에서 계정·설치 ID를 지운다. 판정용 파싱은 이 함수 전의 원문으로 한다."""
    stripped = text.strip()
    if stripped.startswith(('{', '[')):
        try:
            whole = json.loads(stripped)
        except ValueError:
            whole = None
        if whole is not None:
            return json.dumps(scrub(whole), ensure_ascii=False, indent=2) + '\n'
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        try:
            data = json.loads(stripped) if stripped.startswith(('{', '[')) else None
        except ValueError:
            data = None
        if data is not None:
            lines.append(json.dumps(scrub(data), ensure_ascii=False) + ('\n' if line.endswith('\n') else ''))
        else:
            lines.append(re.sub(r'"(%s)"\s*:\s*("[^"]*"|null|\d+),?\s*' % '|'.join(SECRET_KEYS), '', line))
    return ''.join(lines)


def check_run_id(run):
    parts = re.split(r'[\\/]', run)
    if not run or Path(run).is_absolute() or Path(run).drive or run.startswith(('/', '\\')) \
            or not all(RUN_PART.match(p) for p in parts):
        raise Refused(f'invalid run id: {run!r}')
    # 실행기가 임시 폴더에 만드는 항목 이름은 run id에 쓰지 않는다. clean이 중첩 run을 자기 항목으로 오인하지 않게 한다.
    if any(RESERVED_PART.fullmatch(p) for p in parts):
        raise Refused(f'run id part reserved for runner files: {run!r}')
    return '/'.join(parts)


def temp_base():
    return Path(tempfile.gettempdir()).resolve() / 'hglos-cross-host'


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def codex_error_messages(stdout_text):
    """codex --json 이벤트 중 error·turn.failed 메시지만 모은다."""
    messages = []
    for line in stdout_text.splitlines():
        if not line.strip().startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') in ('error', 'turn.failed'):
            error = event.get('error')
            messages.append(str(event.get('message') or (error.get('message') if isinstance(error, dict) else '')))
    return messages


def classify(text):
    low = text.lower()
    for failure, markers in FAILURE_MARKERS:
        if any(m.lower() in low for m in markers):
            return failure
    return None


def family(model):
    m = (model or '').lower()
    if m.startswith('gpt-oss'):
        return 'never'
    if m.startswith('gemini'):
        return 'google'
    if m.startswith('claude'):
        return 'anthropic'
    if m.startswith('gpt'):
        return 'openai'
    return 'unknown'


def claude_base(model):
    match = re.match(r'(claude-[a-z]+-\d+-\d+)', model or '')
    return match.group(1) if match else model


def rank_status(observed, implementer):
    """doc/35 0.3 순위표(doc/33 6.3 갱신). (순위 또는 None, 'ranked' | 'excluded' | 'unknown_grade')."""
    obs, imp = family(observed), family(implementer)
    if obs not in ('anthropic', 'openai'):
        return None, 'excluded'
    if imp == 'anthropic':
        if obs == 'openai':
            return 1, 'ranked'
        if claude_base(observed) == claude_base(implementer):
            return 3, 'ranked'
        # 같은 회사 다른 모델은 사람이 확인한 목록만 2순위다(doc/35 D2). Anthropic 쪽에는 순위 미확인이 없다.
        if claude_base(observed) in SIMILAR_GRADE.get(claude_base(implementer), ()):
            return 2, 'ranked'
        return None, 'excluded'
    if imp == 'openai':
        if obs == 'anthropic':
            return (1, 'ranked') if re.match(r'claude-(fable|opus|sonnet)-', observed) else (None, 'excluded')
        if observed == implementer:
            return 3, 'ranked'
        # 2순위는 구현자와 비슷한 등급으로 확인된 다른 모델만이다(hglos-verify SKILL). 등급은 이름으로
        # 추정하지 않는다. 확인되지 않았으면 제외가 아니라 순위 미확인이다(JH 결정 2026-11-02).
        if observed in SIMILAR_GRADE.get(implementer, ()):
            return 2, 'ranked'
        return None, 'unknown_grade'
    return None, 'excluded'


def rank_of(observed, implementer):
    return rank_status(observed, implementer)[0]


# ------------------------------------------------------------------ 사용량 (6.2)

def app_server_rate_limits(timeout=60):
    """codex app-server와 단계별로 주고받는다: initialize → 응답 → initialized → rateLimits/read → 응답.

    실측(2026-11-02, codex-cli 0.164.0): 요청을 한꺼번에 보내고 stdin을 닫으면 initialize에만 답하고 끝난다.
    반환: (rateLimits result dict 또는 None, 실패 이유 또는 None).
    """
    kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    proc = subprocess.Popen(tool_cmd('codex') + ['app-server'], env=child_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs)
    lines = queue.Queue()

    def pump():
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(message):
        proc.stdin.write((json.dumps(message) + '\n').encode('utf-8'))
        proc.stdin.flush()

    def reply(request_id):
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None, 'timeout'
            try:
                raw = lines.get(timeout=left)
            except queue.Empty:
                return None, 'timeout'
            if raw is None:
                return None, f'app-server ended before reply {request_id}'
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get('id') == request_id:
                if isinstance(msg.get('result'), dict):
                    return msg['result'], None
                return None, f'app-server error for request {request_id}'

    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'hglos-cross-host', 'version': '1'}}})
        result, reason = reply(1)
        if result is not None:
            send({'method': 'initialized'})
            send({'id': 2, 'method': 'account/rateLimits/read'})
            result, reason = reply(2)
        return result, reason
    except OSError as exc:
        return None, f'app-server pipe closed: {exc}'
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            proc.wait(timeout=10)


def codex_usage():
    try:
        result, reason = app_server_rate_limits()
    except (OSError, Refused) as exc:
        return {'status': 'unknown', 'data': None, 'reason': str(exc)}
    limits = (result or {}).get('rateLimits')
    if not isinstance(limits, dict):
        return {'status': 'unknown', 'data': None, 'reason': reason or 'no rateLimits'}

    def window(w):
        if not isinstance(w, dict):
            return None
        return {k: w.get(k) for k in ('usedPercent', 'windowDurationMins', 'resetsAt')}

    data = {'planType': limits.get('planType'), 'primary': window(limits.get('primary')),
            'secondary': window(limits.get('secondary')), 'rateLimitReachedType': limits.get('rateLimitReachedType')}
    return {'status': 'ok', 'data': data}


def agy_usage():
    try:
        code, out, _, timed_out = run_process(tool_cmd('agy') + ['-p', '/usage', '--output-format', 'json'],
                                              cwd=tempfile.gettempdir(), timeout=120)
    except (OSError, Refused) as exc:
        return {'status': 'unknown', 'data': None, 'tokens_consumed': None, 'reason': str(exc)}
    envelope = parse_json_text(out.decode('utf-8', 'replace'))
    if not isinstance(envelope, dict):
        return {'status': 'unknown', 'data': None, 'tokens_consumed': None,
                'reason': 'timeout' if timed_out else f'no JSON envelope (exit {code})'}
    usage = envelope.get('usage') if isinstance(envelope.get('usage'), dict) else {}
    tokens = usage.get('total_tokens')
    command = envelope.get('command') if isinstance(envelope.get('command'), dict) else {}
    groups = (command.get('data') or {}).get('groups') if isinstance(command.get('data'), dict) else None
    ok_groups = isinstance(groups, list) and groups and all(
        isinstance(g, dict) and isinstance(g.get('buckets'), list) and g['buckets']
        and all(isinstance(b, dict) and 'remaining_fraction' in b for b in g['buckets']) for g in groups)
    if command.get('name') != 'usage' or not ok_groups or envelope.get('num_turns') != 0 or tokens != 0:
        return {'status': 'unknown', 'data': None, 'tokens_consumed': tokens,
                'reason': 'not a token-free usage response (6.2)'}
    data = {'groups': [{'name': g.get('name'),
                        'buckets': [{k: b.get(k) for k in ('window', 'remaining_fraction', 'reset_time')}
                                    for b in g['buckets']]} for g in groups]}
    return {'status': 'ok', 'data': data}


def claude_usage(note=None):
    return {'status': 'unknown', 'data': None, 'note': note,
            'alternatives': ['호출 envelope의 usage·total_cost_usd 누적',
                             '한도 오류(limit) 시 소진 표시 후 강등',
                             '사람이 대화형 /usage로 본 값을 --claude-usage-note로 기록']}


def measure(tools, note=None):
    out = {}
    for tool in tools:
        out[tool] = {'codex': codex_usage, 'agy': agy_usage}[tool]() if tool != 'claude' else claude_usage(note)
    return {'tools': out}


def codex_below(entry):
    if entry and entry.get('exhausted'):
        return True  # 호출 뒤 한도 오류로 소진 표시된 기록(usage-after)
    if not entry or entry.get('status') != 'ok':
        return None
    data = entry['data']
    used = [w.get('usedPercent') for w in (data.get('primary'), data.get('secondary')) if isinstance(w, dict)]
    return any(isinstance(u, (int, float)) and u > THRESHOLD_USED_PERCENT for u in used)


def agy_group_below(entry, group):
    """True/False, 그룹이 없거나 측정 불가면 None."""
    if entry and entry.get('exhausted'):
        return True
    if not entry or entry.get('status') != 'ok':
        return None
    for g in entry['data']['groups']:
        if g.get('name') == group:
            return any(isinstance(b.get('remaining_fraction'), (int, float))
                       and b['remaining_fraction'] < THRESHOLD_FRACTION for b in g['buckets'])
    return None


def agy_group_for(model):
    m = model.lower()
    if m.startswith('gemini-4-argon'):
        return AGY_GROUPS['argon']
    if m.startswith('gemini'):
        return AGY_GROUPS['flash']
    return AGY_GROUPS['third_party']


def below_threshold(tool, model, entry):
    if tool == 'codex':
        return codex_below(entry)
    if tool == 'agy':
        return agy_group_below(entry, agy_group_for(model))
    return None


# ------------------------------------------------------------------ 라우팅 (6.2)

def route(usage, implementer, main, kind):
    tools = usage.get('tools', {}) if isinstance(usage, dict) else {}
    fam = 'anthropic' if family(implementer) == 'anthropic' else 'openai'
    order = REVIEW_ROUTES[(fam, main)] if kind == 'review' else CONTEXT_ROUTES[main]
    skipped, evaluated = [], []
    for rid in order:
        model, below, unknown = None, False, False
        if rid == 'claude-p':
            model = DEFAULT_MODEL['claude']
            below = bool((tools.get('claude') or {}).get('exhausted'))
            unknown = not below
        elif rid == 'codex-exec':
            model = DEFAULT_MODEL['codex']
            state = codex_below(tools.get('codex'))
            below, unknown = state is True, state is None
        elif rid == 'agy-claude':
            model = AGY_CLAUDE
            state = agy_group_below(tools.get('agy'), AGY_GROUPS['third_party'])
            below, unknown = state is True, state is None
        elif rid == 'agy-gemini':
            argon = agy_group_below(tools.get('agy'), AGY_GROUPS['argon'])
            flash = agy_group_below(tools.get('agy'), AGY_GROUPS['flash'])
            if argon is False:
                model = AGY_ARGON
            else:
                model = AGY_FLASH
                below, unknown = flash is True, flash is None
        if below:
            skipped.append({'route': rid, 'reason': 'below_threshold'})
        evaluated.append((rid, model, below, unknown))
    chosen = next((e for e in evaluated if not e[2]), None)
    return {'kind': kind, 'implementer_model': implementer, 'main': main, 'order': order,
            'selected': chosen[0] if chosen else None, 'selected_model': chosen[1] if chosen else None,
            'limit_unknown': bool(chosen and chosen[3]), 'skipped': skipped}


# ------------------------------------------------------------------ 요청 구성 (6.1)

def git(repo, *args):
    proc = subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=repo, capture_output=True)
    if proc.returncode != 0:
        raise Refused(f"git {' '.join(args[:2])} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def excluded(path, globs):
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.rstrip('/') + '/*') for g in globs)


def scope_material(repo, scope, globs, own_run=None):
    """범위 diff, 미추적 범위 파일, 사본 대상 파일. 제외 glob 파일과 이 run 자신의 산출 폴더는 모두 뺀다."""
    if not scope:
        return '', [], []
    prefix = own_run.rstrip('/') + '/' if own_run else None

    def keep(p):
        return p and not excluded(p, globs) and not (prefix and (p + '/').startswith(prefix))

    has_head = subprocess.run(['git', 'rev-parse', '--verify', '-q', 'HEAD'], cwd=repo,
                              capture_output=True).returncode == 0
    base = ['HEAD'] if has_head else []
    changed = [p for p in git(repo, 'diff', '--name-only', *base, '--', *scope).decode('utf-8').splitlines()
               if keep(p)]
    diff = git(repo, 'diff', '--no-color', '--no-ext-diff', *base, '--', *changed).decode('utf-8', 'replace') \
        if changed else ''
    untracked = [p for p in git(repo, 'ls-files', '--others', '--exclude-standard', '--', *scope)
                 .decode('utf-8').splitlines() if keep(p)]
    files = sorted({p for p in git(repo, 'ls-files', '-co', '--exclude-standard', '--', *scope)
                    .decode('utf-8').splitlines() if keep(p) and (repo / p).is_file()})
    return diff, untracked, files


def rules_block(kind, canon, globs, untracked, agy):
    task = ('hglos-verify 모드 A 적대 검토. 결과는 findings 스키마 JSON 하나만 낸다.'
            if kind == 'review' else '긴 맥락 읽기·확인. 답은 Markdown으로 낸다.')
    lines = ['', '## HGLOS cross-host 규칙', f'- 작업: {task}',
             '- 읽기 전용이다. 파일을 만들거나 고치지 않는다.',
             '- 다른 세션의 기록(이전 회차 rollout·대화 기록)을 읽지 않는다. Do not read other session records.']
    if agy:
        lines.append('- 검토 자료는 현재 작업 폴더에 있다: scope.diff(범위 diff), files/(범위 파일 사본), '
                     'canon/(정본 사본, 아래 정본 경로 그대로), manifest.md.')
    lines += ['', '## 정본 경로'] + [f'- {c}' for c in canon or ['(없음)']]
    lines += ['', '## 제외 경로 (읽지 않는다)'] + [f'- {g}' for g in globs or ['(없음)']]
    lines += ['', '## 미추적 범위 파일'] + [f'- {u}' for u in untracked or ['(없음)']]
    return '\n'.join(lines) + '\n'


def build_request(tool, kind, instruction, canon, globs, diff, untracked):
    text = instruction.rstrip('\n') + '\n' + rules_block(kind, canon, globs, untracked, tool == 'agy')
    if tool != 'agy':
        text += '\n## 범위 diff\n```diff\n' + diff + '```\n'
    return text


def copy_blocked(repo, rel, globs):
    """패킷에 복사하면 안 되는 이유. symlink(경로 중간 포함)·저장소 밖·제외 glob으로 해석되는 실제 대상."""
    path = repo / rel
    parts = Path(rel).parts
    if any((repo.joinpath(*parts[:i + 1])).is_symlink() for i in range(len(parts))):
        return 'symlink'
    real = Path(os.path.realpath(path))
    if not real.is_relative_to(Path(os.path.realpath(repo))):
        return 'outside repository'
    if excluded(real.relative_to(Path(os.path.realpath(repo))).as_posix(), globs):
        return 'excluded'
    return None


def build_packet(packet, repo, diff, files, untracked, globs, canon=()):
    packet.mkdir(parents=True)
    (packet / 'scope.diff').write_bytes(diff.encode('utf-8'))
    copied, skipped, canon_copied = [], [], []
    for folder, names, sink in (('files', files, copied), ('canon', canon, canon_copied)):
        for rel in names:
            why = 'missing' if not (repo / rel).is_file() else copy_blocked(repo, rel, globs)
            if why:
                skipped.append(f'{folder}/{rel} ({why})')
                continue
            target = packet / folder / rel
            if not Path(os.path.realpath(target)).is_relative_to(Path(os.path.realpath(packet / folder))):
                skipped.append(f'{folder}/{rel} (outside packet)')
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / rel, target, follow_symlinks=False)
            sink.append(rel)
    manifest = ['# packet', '', '## files'] + [f'- files/{f}' for f in copied] + ['', '## untracked'] + \
        [f'- {u}' for u in untracked]
    if canon_copied:
        manifest += ['', '## canon'] + [f'- canon/{c}' for c in canon_copied]
    if skipped:
        manifest += ['', '## skipped (not copied)'] + [f'- {s}' for s in skipped]
    (packet / 'manifest.md').write_text('\n'.join(manifest) + '\n', encoding='utf-8')
    return sum(p.stat().st_size for p in packet.rglob('*') if p.is_file())


# ------------------------------------------------------------------ 선언·run 상태

def load_egress(repo):
    path = repo / '.hglos' / 'cross-host-egress.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'egress declaration missing or invalid: {exc}', 'egress_refused')
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('vendors'), list):
        raise Refused('egress declaration has no schema_version 1 / vendors list', 'egress_refused')
    for key in ('declared_by', 'quote'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise Refused(f'egress declaration needs a non-empty {key}', 'egress_refused')
    if data.get('repo') != repo.name:
        raise Refused(f'egress declaration repo {data.get("repo")!r} is not {repo.name!r}', 'egress_refused')
    globs = data.get('exclude_globs', [])
    # 문자열 하나를 주면 글자 단위로 돌아 제외가 사실상 꺼진다. 목록만 받는다.
    if not isinstance(globs, list) or not all(isinstance(g, str) and g.strip() for g in globs):
        raise Refused('exclude_globs must be a list of non-empty strings', 'egress_refused')
    return data


def required_vendors(tool, model):
    need = {VENDOR[tool]}
    if tool == 'agy' and family(model) == 'anthropic':
        need.add('anthropic')  # U13 D4
    return need


def previous_calls(rundir, n):
    calls = []
    for path in rundir.glob('call_*.json'):
        m = re.fullmatch(r'call_(\d+)\.json', path.name)
        if m and int(m.group(1)) != n:
            try:
                calls.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                raise Refused(f'unreadable {path.name}')
    return sorted(calls, key=lambda c: c.get('round', 0))


def round_files_exist(rundir, n):
    return sorted(p.name for p in rundir.glob('*') if p.name.split('.')[0].endswith(f'_{n}'))


# ------------------------------------------------------------------ 신원 (6.3)

def codex_home():
    env = os.environ.get('CODEX_HOME')
    return (Path(env), '$CODEX_HOME') if env else (Path.home() / '.codex', '~/.codex')


def message_text(payload):
    parts = payload.get('content') or []
    return ''.join(p.get('text', '') for p in parts if isinstance(p, dict))


def codex_identity(thread, requested, request_bytes):
    out = {'model_observed': None, 'effort_observed': None, 'model_source': None, 'model_check': '로그 없음',
           'injected_items': [], 'f3_ok': False, 'f3_reason': 'rollout 없음'}
    if not thread:
        return out
    home, label = codex_home()
    found = sorted((home / 'sessions').rglob(f'rollout-*-{thread}.jsonl')) if (home / 'sessions').is_dir() else []
    if len(found) != 1:
        out['f3_reason'] = 'rollout 없음' if not found else 'rollout 여러 개'
        return out
    path = found[0]
    rows = []
    for i, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        try:
            rows.append((i, json.loads(line)))
        except ValueError:
            continue
    contexts = [(i, r['payload']) for i, r in rows if r.get('type') == 'turn_context' and isinstance(r.get('payload'), dict)]
    out['model_source'] = {'file': f"{label}/{path.relative_to(home).as_posix()}",
                           'lines': [i for i, _ in contexts], 'field': 'payload.model'}
    models = [p.get('model') for _, p in contexts]
    efforts = [p.get('effort') for _, p in contexts if p.get('effort')]
    if not models or any(m is None for m in models):
        return out
    out['model_observed'] = next((m for m in models if m != requested), models[0])
    out['effort_observed'] = efforts[0] if efforts else None
    out['model_check'] = '일치' if all(m == requested for m in models) else '불일치'
    # F3: 첫 assistant 앞의 마지막 user = 실제 요청, 그 앞은 developer 또는 허용 user 주입만.
    messages = [(i, r['payload']) for i, r in rows
                if r.get('type') == 'response_item' and isinstance(r.get('payload'), dict)
                and r['payload'].get('type') == 'message']
    first_assistant = next((k for k, (_, p) in enumerate(messages) if p.get('role') == 'assistant'), len(messages))
    before = messages[:first_assistant]
    users = [k for k, (_, p) in enumerate(before) if p.get('role') == 'user']
    if not users:
        out['f3_reason'] = '실제 요청 없음'
        return out
    request_at = users[-1]
    for i, p in before[:request_at]:
        text = message_text(p)
        out['injected_items'].append({'line': i, 'role': p.get('role'), 'head': text.split('\n', 1)[0][:80],
                                      'sha256': sha256(text.encode('utf-8'))})
    bad = [p.get('role') for _, p in before[:request_at]
           if not (p.get('role') == 'developer' or (p.get('role') == 'user' and message_text(p).startswith(
               ('# AGENTS.md instructions', '<environment_context>'))))]
    if bad:
        out['f3_reason'] = f'허용되지 않은 주입 {len(bad)}건'
    elif message_text(before[request_at][1]).encode('utf-8') != request_bytes:
        out['f3_reason'] = '실제 요청 해시 불일치'
    else:
        out['f3_ok'], out['f3_reason'] = True, None
    return out


def claude_events(stdout_text):
    events = []
    for i, line in enumerate(stdout_text.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append((i, event))
    return events


def claude_identity(events, requested, stream_name):
    assistants = [(i, e) for i, e in events if e.get('type') == 'assistant' and isinstance(e.get('message'), dict)]
    result = next((e for _, e in reversed(events) if e.get('type') == 'result'), {})
    usage = result.get('modelUsage') if isinstance(result.get('modelUsage'), dict) else {}
    observed_set = sorted(usage) or None
    out = {'model_observed': None, 'model_observed_set': observed_set, 'model_source': None, 'model_check': '로그 없음'}
    if not assistants:
        return out
    line, last = assistants[-1]
    final = last['message'].get('model')
    out['model_source'] = {'file': stream_name, 'lines': [line], 'field': 'message.model'}
    if not final:
        out['model_check'] = '최종 작성자 미확인' if observed_set else '로그 없음'
        return out
    out['model_observed'] = final
    out['model_check'] = '일치' if final == requested else '불일치'
    return out


def agy_identity(log, packet, requested, rel):
    out = {'model_observed': None, 'model_source': None, 'model_check': '로그 없음'}
    if not log.is_file():
        return out
    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    workspaces = [m.group(1).strip() for m in (re.search(r'Initializing CLI store manager for workspace (.+)$', l)
                                               for l in lines) if m]
    if not workspaces or any(os.path.normcase(os.path.abspath(w)) != os.path.normcase(str(packet.resolve()))
                             for w in workspaces):
        return out
    resolved = [(i, m.group(1)) for i, m in ((i, re.search(r'model_resolver\.go:\d+\] Resolving model (\S+)', l))
                                             for i, l in enumerate(lines, 1)) if m]
    if not resolved:
        return out
    models = [m for _, m in resolved]
    out['model_source'] = {'file': rel, 'lines': [i for i, _ in resolved], 'field': 'Resolving model'}
    out['model_observed'] = next((m for m in models if m != requested), models[0])
    out['model_check'] = '일치' if all(m == requested for m in models) else '불일치'
    return out


# ------------------------------------------------------------------ 결과 추출·계약

def parse_json_text(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def validate_findings(raw):
    proc = subprocess.run([sys.executable, '-B', str(verify_script('validate_findings.py')), str(raw),
                           '--schema', str(schema_path())], capture_output=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).decode('utf-8', 'replace').strip()


def make_round(repo, rundir, raw, n):
    rel = rundir.relative_to(repo).as_posix()
    proc = subprocess.run([sys.executable, '-B', str(verify_script('make_round_json.py')), str(raw),
                           '--round', str(n), '--outdir', rel], cwd=repo, capture_output=True)
    path = rundir / f'round_{n}.json'
    if proc.returncode != 0 or not path.is_file():
        return None, ['make_round_json failed: ' + proc.stderr.decode('utf-8', 'replace').strip()]
    data = json.loads(path.read_text(encoding='utf-8'))
    return path, list(data.get('contract_violations') or [])


# ------------------------------------------------------------------ 무결성

def repo_state(repo, rundir):
    rel = rundir.relative_to(repo).as_posix()
    head = subprocess.run(['git', 'rev-parse', '-q', '--verify', 'HEAD'], cwd=repo, capture_output=True).stdout.strip()
    status = git(repo, 'status', '--porcelain=v1', '-uall', '-z', '--', '.', f':(exclude){rel}')
    entries = []
    for item in status.decode('utf-8', 'replace').split('\0'):
        if len(item) < 4:
            continue
        path = item[3:]
        target = repo / path
        digest = sha256(target.read_bytes()) if target.is_file() else 'absent'
        entries.append(f'{item[:2]} {path} {digest}')
    # index만 바꾼 쓰기(update-index, 같은 상태 코드의 재스테이징)도 잡도록 index 항목(모드·blob·stage·경로)을 더한다.
    # .git/index 바이트는 git status의 stat 갱신으로도 바뀌므로 쓰지 않는다.
    index = git(repo, 'ls-files', '-s', '-z', '--', '.', f':(exclude){rel}')
    return {'head': head.decode(), 'entries': len(entries), 'sha256': sha256('\n'.join(entries).encode('utf-8')),
            'index_sha256': sha256(index)}


# ------------------------------------------------------------------ call

def write_new(path, data):
    """덮어쓰지 않는다."""
    with open(path, 'xb') as handle:
        handle.write(data if isinstance(data, bytes) else data.encode('utf-8'))


def write_json(path, data):
    write_new(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def header(record, version, requested_rank):
    src = record['model_source']
    where = f"{src['file']}:{','.join(map(str, src['lines']))} {src['field']}" if src else '출처 없음'
    observed = record['model_observed'] or (('{' + ', '.join(record['model_observed_set']) + '}')
                                             if record.get('model_observed_set') else '없음')
    if record['rank'] is not None:
        rank = str(record['rank'])
    elif record['kind'] == 'context':
        rank = '해당 없음(긴 맥락 확인)'
    elif record['model_check'] == '일치':
        rank = '미확인(등급 미확인)' if record.get('rank_status') == 'unknown_grade' else '제외'
    else:
        rank = f"미확인({record['model_check']} · 요청 기준 {requested_rank if requested_rank else '제외'})"
    tail = '' if record['formal'] or record['kind'] == 'context' else ' · 정식 회차 아님'
    implementer = record.get('implementer_model')
    if record['model_check'] == '일치' and implementer:
        same = '예' if family(record['model_observed']) == family(implementer) else '아니오'
    else:
        same = '미확인'
    return (f"검증자: {record['tool']} {version or '버전 미확인'} · 요청 모델 {record['model_requested']} · "
            f"관측 모델 {observed}({where}) · 판정: {record['model_check']} · 순위: {rank} · 세션: 새 세션 · "
            f"구현자와 같은 패밀리: {same} · 관측 출처: CLI 로컬 기록{tail}")


def call(args):
    repo = Path(args.repo).resolve()
    run = check_run_id(args.run)
    n = args.round
    if n < 1:
        raise Refused('round must be >= 1')
    tool, kind = args.tool, args.kind
    rundir = repo / 'validation' / Path(run)
    tmprun = temp_base() / Path(run)
    if not inside(rundir, repo / 'validation') or not inside(tmprun, temp_base()):
        raise Refused(f'invalid run id: {run!r}')
    if kind == 'review' and not args.implementer_model:
        raise Refused('--implementer-model is required for review')
    if not args.scope:
        raise Refused('--scope is required: without it the request carries no review material')
    # 범위·정본은 저장소 기준 상대 경로만 받는다. '..'이나 절대 경로는 agy 패킷 복사 목적지를 밖으로 빼낼 수 있다.
    for rel in [*args.scope, *(args.canon or [])]:
        parts = re.split(r'[\\/]', rel)
        if not rel or Path(rel).is_absolute() or Path(rel).drive or rel.startswith(('/', '\\')) or '..' in parts:
            raise Refused(f'--scope/--canon must be repository-relative without "..": {rel!r}')
    instruction = Path(args.instruction).read_text(encoding='utf-8')
    if rundir.exists():
        existing = round_files_exist(rundir, n)
        if existing:
            raise Refused(f'round {n} files already exist, not overwriting: {existing}')
    previous = previous_calls(rundir, n) if rundir.exists() else []
    if any(c.get('tool') != tool for c in previous):
        raise Refused('this run already uses another tool; a downgrade starts a new run (6.4)')
    if any(c.get('kind') == 'review' and c.get('failure') == 'identity_unconfirmed' for c in previous):
        raise Refused('this run stopped after identity_unconfirmed; start a new run (6.3 f)')
    if any(c.get('failure') == 'integrity' for c in previous):
        raise Refused('this run stopped after an integrity failure (repository or review target changed); '
                      'start a new run')
    if any(c.get('round') == n for c in previous):
        raise Refused(f'round {n} already recorded')
    packet = tmprun / f'agy-packet-{n}'
    if tool == 'agy' and inside(tmprun, repo):
        raise Refused('agy packet folder would be inside the repository; set TEMP outside the repository')
    if tool == 'agy' and packet.exists():
        raise Refused(f'packet folder already exists: agy-packet-{n}')
    model = args.model or DEFAULT_MODEL.get(tool)
    if not model:
        raise Refused('--model is required for agy')
    effort = (args.effort or DEFAULT_EFFORT) if tool == 'codex' else args.effort
    timeout = args.timeout or TIMEOUT[kind]
    rundir.mkdir(parents=True, exist_ok=True)

    record = {'round': n, 'tool': tool, 'kind': kind, 'run': run, 'version': None, 'argv': None, 'cwd': None,
              'started_at': None, 'ended_at': None, 'exit_code': None, 'failure': None,
              'timeout_seconds': timeout, 'input_checks': [], 'model_requested': model, 'model_observed': None,
              'model_observed_set': None, 'model_source': None, 'model_check': None,
              'effort_requested': effort, 'effort_observed': None, 'formal': False, 'formal_reason': None,
              'round_json': None, 'rank': None, 'rank_status': None, 'downgraded_from': args.downgraded_from,
              'implementer_model': args.implementer_model, 'thread_id': None, 'conversation_id': None,
              'request_sha256': None, 'target_sha256': None, 'injected_items': [], 'session_persistence':
                  {'codex': '보존(rollout, U12)', 'claude': '비보존(--no-session-persistence)', 'agy': '불가'}[tool],
              'header': None}

    def finish(failure, reason, code):
        record['failure'] = failure
        if reason and not record['formal_reason']:
            record['formal_reason'] = reason
        record['header'] = header(record, record['version'], rank_of(model, args.implementer_model)
                                  if args.implementer_model else None)
        write_json(rundir / f'call_{n}.json', record)
        print(json.dumps({'round': n, 'failure': failure, 'formal': record['formal'],
                          'model_check': record['model_check'], 'rank': record['rank']}, ensure_ascii=False))
        if reason:
            print(reason, file=sys.stderr)
        return code

    # 호출 전 거절: 전송 선언, 입력 상한, 사용량(토큰 0), 사용 금지 모델. 모두 모델 호출 전이다.
    try:
        egress = load_egress(repo)
    except Refused as exc:
        return finish(exc.failure, str(exc), 2)
    missing = required_vendors(tool, model) - set(egress['vendors'])
    if missing:
        return finish('egress_refused', f'전송 선언에 없는 벤더: {sorted(missing)}', 2)
    globs = egress.get('exclude_globs') or []
    diff, untracked, files = scope_material(repo, args.scope, globs, own_run=rundir.relative_to(repo).as_posix())
    # 같은 run은 같은 검토 대상만 다룬다(hglos-verify). 대상이 바뀌면 새 run이다.
    def fingerprint(diff, files):
        target = {'head': subprocess.run(['git', 'rev-parse', '-q', '--verify', 'HEAD'], cwd=repo,
                                         capture_output=True).stdout.decode().strip(),
                  'kind': kind, 'scope': sorted(args.scope), 'canon': sorted(args.canon or []),
                  'exclude_globs': globs, 'diff_sha256': sha256(diff.encode('utf-8')),
                  'canon_files': {c: (sha256((repo / c).read_bytes()) if (repo / c).is_file() else 'absent')
                                  for c in sorted(args.canon or [])},
                  'files': {f: sha256((repo / f).read_bytes()) for f in files}}
        return sha256(json.dumps(target, sort_keys=True).encode('utf-8'))

    record['target_sha256'] = fingerprint(diff, files)
    earlier = next((c.get('target_sha256') for c in previous if c.get('target_sha256')), None)
    if earlier and earlier != record['target_sha256']:
        return finish('integrity', '검토 대상이 run 1회차 기준(target_sha256)과 다름: 호출하지 않음, 새 run 필요', 2)
    request = build_request(tool, kind, instruction, args.canon or [], globs, diff, untracked)
    request_bytes = request.encode('utf-8')
    if tool == 'agy':
        packet_bytes = build_packet(packet, repo, diff, files, untracked, globs, args.canon or [])
        checks = [{'name': 'prompt_chars', 'measured': len(request), 'limit': PROMPT_LIMIT, 'unit': 'chars'},
                  {'name': 'packet_bytes', 'measured': packet_bytes, 'limit': PACKET_LIMIT, 'unit': 'bytes'}]
    else:
        checks = [{'name': 'request_bytes', 'measured': len(request_bytes), 'limit': REQUEST_LIMIT, 'unit': 'bytes'}]
    for c in checks:
        c['ok'] = c['measured'] <= c['limit']
    record['input_checks'] = checks
    if not all(c['ok'] for c in checks):
        return finish('input_too_large', '입력 상한 초과: ' + ', '.join(
            f"{c['name']} {c['measured']}>{c['limit']} {c['unit']}" for c in checks if not c['ok']), 2)
    before_usage = measure([tool])
    write_json(rundir / f'usage-before_{n}.json', before_usage)
    if below_threshold(tool, model, before_usage['tools'][tool]):
        exhausted = json.loads(json.dumps(before_usage))
        exhausted['tools'][tool]['exhausted'] = True
        write_json(rundir / f'usage-after_{n}.json', exhausted)
        return finish('limit', '사용량 임계(10%) 미달 (6.2)', 2)
    fam = family(model)
    if tool == 'agy' and (fam == 'never' or (kind == 'review' and fam == 'google')):
        return finish('identity_unconfirmed', f'사용하지 않는 모델 {model} (3절, U11, U16)', 2)

    # 호출.
    write_new(rundir / f'request_{n}.md', request_bytes)
    record['request_sha256'] = sha256(request_bytes)
    record['version'] = tool_version(tool)
    schema = schema_path()
    result_name = f'{tool}_{n}.raw.json' if kind == 'review' else f'{tool}_{n}.answer.md'
    result_path = rundir / result_name
    stdin, cwd = request_bytes, repo
    if tool == 'codex':
        argv = ['exec', '-m', model, '-c', f'model_reasoning_effort="{effort}"', '--sandbox', 'read-only',
                '--disable', 'memories', '-c', 'memories.use_memories=false',
                '-c', 'memories.generate_memories=false', '--cd', str(repo), '--json']
        if kind == 'review':
            argv += ['--output-schema', str(schema)]
        argv += ['-o', str(result_path), '-']
    elif tool == 'claude':
        argv = ['-p', '--model', model, '--permission-mode', 'plan', '--restricted', '--tools', CLAUDE_TOOLS,
                '--strict-mcp-config', '--output-format', 'stream-json', '--verbose', '--no-session-persistence']
        if kind == 'review':
            compact = json.dumps(json.loads(schema.read_text(encoding='utf-8')), separators=(',', ':'),
                                 ensure_ascii=False)
            argv += ['--json-schema', compact]
    else:
        log = packet / f'agy_{n}.cli.txt'
        argv = ['-p', request, '--mode', 'plan', '--sandbox', '--model', model, '--output-format', 'json',
                '--log-file', str(log), '--print-timeout', f'{max(timeout - AGY_PRINT_MARGIN, 1)}s']
        if kind == 'review':
            schema_copy = tmprun / f'agy-schema-{n}.json'
            write_new(schema_copy, schema.read_bytes())
            argv += ['--json-schema', str(schema_copy)]
        stdin, cwd = None, packet

    def private(value):
        text = str(value)
        for form in {str(tmprun), tmprun.as_posix()}:
            text = text.replace(form, '<TMP_RUN>')
        return text

    record['argv'] = [private(a) for a in argv] if tool == 'agy' else argv
    record['cwd'] = private(cwd) if tool == 'agy' else str(cwd)
    integrity_before = repo_state(repo, rundir)
    # 지시문을 만든 뒤(사용량 조회 등) 호출 직전까지 검토 대상이 바뀌었으면 보낸 지시문과 CLI가 읽을 내용이 다르다.
    now_diff, _, now_files = scope_material(repo, args.scope, globs, own_run=rundir.relative_to(repo).as_posix())
    if fingerprint(now_diff, now_files) != record['target_sha256']:
        return finish('integrity', '지시문을 만든 뒤 호출 직전까지 검토 대상이 바뀜: 호출하지 않음, 새 run 필요', 2)
    record['started_at'] = now()
    start_error = None
    clock = time.monotonic()
    try:
        soft = max(timeout - AGY_PRINT_MARGIN, 1) if tool == 'agy' else None
        code, out, err, timed_out = run_process(tool_cmd(tool) + argv, cwd=cwd, stdin=stdin, timeout=timeout,
                                                soft_deadline=soft)
    except (OSError, Refused) as exc:
        start_error = str(exc)
        code, out, err, timed_out = None, b'', start_error.encode('utf-8'), False
    elapsed = time.monotonic() - clock
    record['ended_at'] = now()
    record['exit_code'] = code
    integrity_after = repo_state(repo, rundir)
    changed = integrity_before != integrity_after
    write_json(rundir / f'integrity_{n}.json', {'before': integrity_before, 'after': integrity_after,
                                                 'changed': changed})
    # 판정은 원문(out_raw·err_raw)으로 하고, 저장 사본만 식별자를 지운다.
    out_raw, err_raw = out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')
    out_text, err_text = scrub_text(out_raw), scrub_text(err_raw)
    write_new(rundir / f'{tool}_{n}.stdout.txt', out_text)
    write_new(rundir / f'{tool}_{n}.stderr.txt', err_text)

    failure, reason, answer = None, None, None
    if start_error:
        failure, reason = 'contract', f'CLI 실행 실패: {start_error}'
    elif timed_out:
        failure, reason = 'timeout', f'실행기 시간 제한 {timeout}초 초과, 프로세스 트리 종료'

    # 도구별 추출과 신원.
    if tool == 'codex':
        thread = None
        for line in out_raw.splitlines():
            event = parse_json_text(line) if line.strip().startswith('{') else None
            if isinstance(event, dict) and event.get('type') == 'thread.started':
                thread = event.get('thread_id')
                break
        record['thread_id'] = thread
        ident = codex_identity(thread, model, request_bytes)
        record.update({k: ident[k] for k in ('model_observed', 'effort_observed', 'model_source',
                                              'model_check', 'injected_items')})
        if not failure and code != 0:
            # stdout에는 검증자가 읽은 소스 원문이 섞이므로 분류는 stderr와 실패 이벤트 메시지로만 한다.
            failure = classify(err_raw + '\n' + '\n'.join(codex_error_messages(out_raw))) or 'contract'
            reason = f'codex exit {code}'
        if not failure:
            if not result_path.is_file() or not result_path.read_bytes().strip():
                failure, reason = 'contract', '결과 파일이 없거나 비어 있음'
    elif tool == 'claude':
        write_new(rundir / f'claude_{n}.stream.jsonl', out_text)
        events = claude_events(out_raw)
        record.update(claude_identity(events, model, f'claude_{n}.stream.jsonl'))
        result = next((e for _, e in reversed(events) if e.get('type') == 'result'), None)
        init = next((e for _, e in events if e.get('type') == 'system'), {})
        record['conversation_id'] = (result or {}).get('session_id') or init.get('session_id')
        if not failure and result and result.get('subtype') == 'error_max_budget_usd':
            failure, reason = 'limit', 'error_max_budget_usd'
        elif not failure and (code != 0 or not result or result.get('is_error')):
            failure = classify(err_raw + json.dumps(result or {}, ensure_ascii=False)) or 'contract'
            reason = f'claude exit {code}'
        if not failure:
            if kind == 'review':
                structured = result.get('structured_output')
                if not isinstance(structured, dict):
                    failure, reason = 'contract', 'structured_output 없음'
                else:
                    answer = json.dumps(structured, ensure_ascii=False)
            else:
                answer = result.get('result') if isinstance(result.get('result'), str) else ''
    else:
        envelope = parse_json_text(out_raw)
        if isinstance(envelope, dict):
            write_new(rundir / f'agy_{n}.envelope.json', json.dumps(scrub(envelope), ensure_ascii=False, indent=2) + '\n')
            record['conversation_id'] = envelope.get('conversation_id') or None
        record.update(agy_identity(packet / f'agy_{n}.cli.txt', packet, model, f'agy-packet-{n}/agy_{n}.cli.txt'))
        if not failure and code != 0:
            print_limit = max(timeout - AGY_PRINT_MARGIN, 1)
            if elapsed >= print_limit:
                failure, reason = 'timeout', f'agy --print-timeout {print_limit}s 도달(경과 {elapsed:.0f}초)'
            else:
                # envelope의 response는 모델 출력이므로 분류에 쓰지 않는다.
                meta = {k: v for k, v in envelope.items() if k != 'response'} if isinstance(envelope, dict) else {}
                failure = classify(err_raw + '\n' + json.dumps(meta, ensure_ascii=False)) or 'contract'
                reason = f'agy exit {code}'
        if not failure:
            if not isinstance(envelope, dict) or not isinstance(envelope.get('response'), str):
                failure, reason = 'contract', 'envelope response 없음'
            else:
                answer = envelope['response']
    if not failure and answer is not None:
        if not answer.strip():
            failure, reason = 'contract', '빈 응답'
        else:
            write_new(result_path, answer)

    # 호출 중 저장소가 바뀌면 읽기 전용 위반이다. 결과를 믿지 않고 run을 멈춘다(validator.md 2.5단계와 같은 처리).
    if changed:
        failure, reason = 'integrity', '호출 전후 저장소 상태가 다름(읽기 전용 위반 의심)'

    # 계약 검사와 정식 회차 판정.
    if not failure and kind == 'review':
        ok, detail = validate_findings(result_path)
        if not ok:
            failure, reason = 'contract', detail
    if kind == 'review' and record['model_check'] == '일치':
        record['rank'], record['rank_status'] = rank_status(record['model_observed'], args.implementer_model)
    # 신원 판정은 출력 계약 실패가 있어도 한다. 관측 증거가 있는 신원 문제는 contract보다 우선해 run을 멈춘다.
    # (CLI 시작 실패·로그 자체가 없는 경우는 신원 증거가 없으므로 contract로 둔다.)
    evidence = record['model_observed'] is not None or record['model_check'] == '최종 작성자 미확인'
    if failure is None or (failure == 'contract' and not start_error and evidence):
        contract_reason = reason if failure == 'contract' else None
        problems = []
        if record['model_check'] != '일치':
            problems.append(f"신원 판정 {record['model_check']}")
        if kind == 'review':
            if record.get('rank_status') == 'excluded':
                problems.append(f"순위표 제외 모델 {record['model_observed']}")
            reference = next((c.get('model_observed') for c in previous if c.get('kind') == 'review'
                              and c.get('model_check') == '일치'), None)
            if reference and record['model_observed'] != reference:
                problems.append(f'run 1회차 모델 {reference}와 다름')
            if tool == 'codex':
                if record['thread_id'] in {c.get('thread_id') for c in previous}:
                    problems.append('F1: 같은 run의 앞 회차와 thread_id가 같음')
                if not ident['f3_ok']:
                    problems.append(f"F3: {ident['f3_reason']}")
        if problems:
            record['rank'] = None
            failure, reason = 'identity_unconfirmed', '; '.join(problems) + (
                f' (출력 계약 실패도 있음: {contract_reason})' if contract_reason else '')
    if not failure and kind == 'review':
        round_path, violations = make_round(repo, rundir, result_path, n)
        if violations:
            failure, reason = 'contract', '; '.join(violations)
            record['rank'] = None
        else:
            record['formal'], record['round_json'] = True, round_path.name
    if failure:
        record['rank'] = None
    if kind == 'context' and not failure:
        record['formal_reason'] = '긴 맥락 확인은 검증 회차가 아님'

    after_usage = measure([tool])
    if failure == 'limit':
        after_usage['tools'][tool]['exhausted'] = True
    write_json(rundir / f'usage-after_{n}.json', after_usage)
    return finish(failure, reason, 1 if failure else 0)


# ------------------------------------------------------------------ CLI

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='command', required=True)
    c = sub.add_parser('call')
    c.add_argument('--repo', required=True)
    c.add_argument('--run', required=True)
    c.add_argument('--round', type=int, required=True)
    c.add_argument('--tool', choices=TOOLS, required=True)
    c.add_argument('--kind', choices=('review', 'context'), required=True)
    c.add_argument('--instruction', required=True)
    c.add_argument('--scope', action='append')
    c.add_argument('--canon', action='append')
    c.add_argument('--implementer-model')
    c.add_argument('--model')
    c.add_argument('--effort')
    c.add_argument('--timeout', type=int)
    c.add_argument('--downgraded-from')
    u = sub.add_parser('usage')
    u.add_argument('--tools', default='codex,agy,claude')
    u.add_argument('--claude-usage-note')
    r = sub.add_parser('route')
    r.add_argument('--usage', required=True)
    r.add_argument('--implementer-model', required=True)
    r.add_argument('--main', choices=('codex', 'claude'), required=True)
    r.add_argument('--kind', choices=('review', 'context'), required=True)
    k = sub.add_parser('clean')
    k.add_argument('--run', required=True)
    args = ap.parse_args(argv)
    try:
        if args.command == 'call':
            return call(args)
        if args.command == 'usage':
            tools = [t.strip() for t in args.tools.split(',') if t.strip()]
            if not tools or set(tools) - set(TOOLS):
                raise Refused(f'unknown tools: {args.tools}')
            print(json.dumps(measure(tools, args.claude_usage_note), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'route':
            usage = json.loads(Path(args.usage).read_text(encoding='utf-8'))
            print(json.dumps(route(usage, args.implementer_model, args.main, args.kind), ensure_ascii=False, indent=2))
            return 0
        run = check_run_id(args.run)
        target = temp_base() / Path(run)
        if not inside(target, temp_base()) or target.resolve() == temp_base():
            raise Refused(f'invalid run id: {run!r}')
        # 이 run이 만든 항목만 지운다. 중첩된 다른 run 폴더(예: <RUN>/<하위 run>)는 신원 근거이므로 남긴다.
        kept = []
        if target.is_dir():
            for entry in target.iterdir():
                own = (entry.is_dir() and re.fullmatch(r'agy-packet-\d+', entry.name)) or entry.name == 'canary' \
                    or (entry.is_file() and re.fullmatch(r'agy-schema-\d+\.json', entry.name))
                if own and not entry.is_symlink():
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
                else:
                    kept.append(entry.name)
            if not kept:
                target.rmdir()
        print(json.dumps({'run': run, 'removed': not target.exists(), 'kept': sorted(kept)}, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
