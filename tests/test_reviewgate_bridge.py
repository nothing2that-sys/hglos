"""doc/34 task O oracle candidate - NOT FROZEN until JH confirms this file and the
fixtures under tests/fixtures/reviewgate/ by SHA-256.

Covers doc/34 section 2 conditions 1-6 (6.1, 6.2) with a fake ReviewGate CLI only.
No real ReviewGate, model or network is used. All identifiers are synthetic:
UUIDs 00000000-0000-4000-8000-0000000000NN, run ids lrun_000..., attempt ids
attempt_000..., home paths only under C:\\Users\\example (synthetic).

Public interface proposed here (plugin/hglos/scripts/reviewgate_bridge.py, stdlib only):
  review    --run-dir D --repo R --instruction FILE --reviewer SPEC [--reviewer SPEC]
            --input REL [--input REL]... [--reference REL]... --profile code|document
            --implementer-model M [--diff-base REV] [--timeout SEC]
  implement --run-dir D --repo R --instruction FILE --worker SPEC --reviewer SPEC [--reviewer SPEC]
            [--input REL]... [--reference REL]... [--check CMD]... [--base REV | --branch B]
            [--timeout SEC]
  discard   --run-dir D --repo R --branch B [--confirmed-by TEXT]
  status    --run-dir D
  SPEC = <provider> or <provider>=<full model id>. Providers: codex-cli, claude-cli (CLI),
  openai, together (API, model required), agy-cli, google (always refused).
Executable: only the environment variable REVIEWGATE_CLI (absolute path). No PATH lookup.
Bridge stdout: one JSON object with at least "classification", "exit" and "round"
  (the call number N, null for refusals before any file is written).
Bridge exit: 0 ok, 1 timeout / failed / review_partial / partial / checks_failed /
  reviewgate_busy / needs_reconciliation, 2 refusals / contract / input_too_large,
  3 approval_required / checkout_changed, 4 reviewgate_unavailable.
Refusals before the call (egress_refused, model_required, provider_on_hold, input_required,
  same_vendor, confirmation_required) do not start ReviewGate and write nothing in the run folder.
Run folder: --run-dir is created if missing. N = 1 + the highest existing call_<k>.json.
  Files never overwritten (existing file -> contract, exit 2, ReviewGate not started):
  request_<N>.md, reviewgate_<N>.stdout.json (raw stdout bytes), reviewgate_<N>.stderr.txt,
  export_<N>.stdout.json, export_<N>.stderr.txt, call_<N>.json, and for formal rounds only
  reviewgate_<N>.raw.json (export copy) and round_<N>.json.
  request_<N>.md starts with the --instruction bytes; with --diff-base it also carries
  `git diff <rev> -- <inputs>` and the names of untracked input files (not their content).
ReviewGate argv (fake sees): review --prompt-file <run>/request_<N>.md --source-root R
  --repository-root R --reviewer ... --input ... [--reference ...] --profile P --no-fallback;
  implement --prompt-file ... --repo R --worker W --reviewer ... [--input ...] [--reference ...]
  [--check ...] [--base X | --branch B] --no-fallback (never --max-minutes, never --no-review);
  export --run <run_id> --format hglos-findings (only for formal candidates, never --allow-partial);
  discard --repo R --branch B; policy status. cwd = R for review/implement/discard.
  The child environment is passed through (FAKE_RG_MARK arrives unchanged).
call_<N>.json keys used here: command, argv (argv[0] == "$REVIEWGATE_CLI"), exit_code, status,
  error_code, classification, reason (reviewgate_unavailable: not_installed or the error_code),
  run_id, branch, checks, violations, confirmed_by, reviewers_requested, worker_requested,
  attempts[{attempt_id, model_identity, model_check, f3{ok} (codex only)}], model_check,
  model_observed, rank, formal_round, formal_reasons, header, integrity{changed},
  worker_model_check (implement), export{exit_code, error_code}.
Requested model record: "<model id>" for SPEC with a model, "default:<provider>" otherwise.
formal_reasons codes (prefix match): not_candidate, not_ok:<classification>, model_check:<value>,
  rank_excluded, run_stopped, model_changed, session_id, f3, fallback, resumed, request_sha256,
  integrity, export_incomplete:<error_code>, round_tools_missing, contract:<text>.
Formal candidate (doc/34 0.1): review with exactly one reviewer given as provider=model and
  --profile code. Only candidates call export; only formal rounds produce round_<N>.json.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / 'plugin/hglos/scripts/reviewgate_bridge.py'
CROSS_HOST = REPO / 'plugin/hglos/scripts/cross_host.py'
FIX = REPO / 'tests/fixtures/reviewgate'
FAKE = FIX / 'fake_reviewgate.py'

SOL, TERRA, LUNA = 'gpt-6.1-sol', 'gpt-6.2-terra', 'gpt-6-luna'
OPUS, SONNET, HAIKU = 'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5'
RUN_ID = 'lrun_00000000000000000000000000000001'
INSTRUCTION = 'SYNTHETIC-INSTRUCTION-MARKER 이 변경을 검토하라.\n'
BRANCH = 'reviewgate/impl-20260101-000000-000001'
VALID_FINDINGS = {'findings': [{'path': 'src/app.py', 'line': 2, 'category': 'logic', 'severity': 'low',
                                'evidence': 'observed', 'claim': 'Synthetic claim',
                                'detail': 'Synthetic detail.'}]}
DEFAULT = '로그 없음(요청 모델 default)'


def tid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def values(args, name):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


def pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ------------------------------------------------------------ ReviewGate output builders

def identity(provider, requested, final, observed=None, source='codex', k=0, session=None, cli_default=False,
             fallback=False, resumed=False, request_sha='@REQUEST_SHA256@'):
    if source == 'codex':
        src = {'file': '~/.codex/sessions/2026/11/06/rollout.jsonl', 'path': f'@ROLLOUT_PATH:{k}@',
               'line': 2, 'lines': [2, 8], 'field': 'turn_context.payload.model'}
    elif source == 'claude':
        src = {'file': None, 'stream': 'stdout', 'line': 7, 'field': 'assistant.message.model',
               'observed_field': 'result.modelUsage'}
    elif source == 'api':
        src = {'file': None, 'stream': 'response_body', 'line': None, 'field': 'model'}
    else:
        src = None
    mi = {'provider': provider, 'cli_version': None if source == 'api' else 'synthetic 0.0.0',
          'model_requested': requested, 'effort_requested': None,
          'model_observed': [final] if observed is None and final else (observed or []),
          'model_final': final, 'effort_observed': None, 'source': src,
          'session_id': session or tid(k + 1), 'resumed': resumed,
          'sent_prompt_sha256': f'@SENT_SHA256:{k}@' if source == 'codex' else sha(b'synthetic sent'),
          'request_sha256': request_sha, 'fallback_used': fallback}
    if cli_default:
        mi['cli_default_model'] = True
    return mi


def attempt(k, mi, reviewer='codex-cli', fallback=False):
    a = {'attempt_id': f'attempt_{k + 1:032d}', 'request_id': f'request_{k + 1:032d}',
         'unit_id': f'unit_{k + 1}', 'reviewer': reviewer, 'phase': 'settled', 'error_code': None,
         'fallback_used': fallback}
    if mi is not None:
        a['model_identity'] = mi
    return a


def codex_attempts(n=1, model=SOL, requested=SOL, variants=None, sessions=None, cli_default=False):
    attempts, rollouts = [], []
    for k in range(n):
        session = (sessions or {}).get(k, tid(k + 1))
        attempts.append(attempt(k, identity('codex-cli', requested, model, k=k, session=session,
                                            cli_default=cli_default)))
        rollouts.append({'session': session, 'model': model, 'variant': (variants or {}).get(k, 'ok')})
    return attempts, rollouts


def review_resp(attempts, rollouts=(), status='completed', error_code=None, exit=0, touch=None,
                request_sha='@REQUEST_SHA256@', run_id=RUN_ID):
    out = {'schema_version': '1.0', 'command': 'review', 'execution_mode': 'live', 'run_id': run_id,
           'plan_id': 'lplan_00000000000000000000000000000001', 'status': status, 'error_code': error_code,
           'artifacts': [], 'cost': {'usage_calculated_usd': 0, 'unknown_hold_usd': 0, 'mode': 'live'},
           'details': {'report_path': '@WORK@/report.md', 'unit_notes': [],
                       'findings': [{'reviewer': 'codex-cli', 'kind': 'defect', 'severity': 'low',
                                     'location': 'src/app.py:2', 'claim': 'Synthetic claim',
                                     'detail': 'Synthetic detail.', 'quote': 'SCOPED_CHANGE_MARKER = 1',
                                     'evidence': 'synthetic'}],
                       'request_sha256': request_sha, 'attempts': list(attempts)}}
    resp = {'exit': exit, 'stdout': out, 'stderr': 'SYNTHETIC-STDERR\n', 'rollouts': list(rollouts)}
    if touch:
        resp['touch'] = touch
    return resp


def export_resp(content=None, exit=0, status='completed', error_code=None, artifacts=None):
    out = {'schema_version': '1.0', 'command': 'export', 'execution_mode': 'live', 'run_id': RUN_ID,
           'status': status, 'error_code': error_code,
           'artifacts': artifacts if artifacts is not None else
           [{'path': f'exports/{RUN_ID}/hglos.findings.live.json', 'absolute_path': '@EXPORT_PATH@',
             'sha256': '@EXPORT_SHA256@', 'bytes': '@EXPORT_BYTES@', 'kind': 'json'}],
           'details': None}
    resp = {'exit': exit, 'stdout': out}
    if exit == 0:
        resp['export_file'] = {'content': VALID_FINDINGS if content is None else content}
    return resp


def impl_resp(status='completed', exit=0, worker_status='completed', answer_status='done', skipped=(),
              review_status='completed', review=True, checks_passed=True, unchanged=True, worker_mi=None,
              violations=None, error_code=None):
    details = {'repository': 'repo', 'branch': BRANCH, 'base_commit': '0' * 40, 'commit': '1' * 40,
               'diff_path': '@WORK@/impl.diff', 'diff_stat': ' calc.py | 2 +-',
               'changed_files': [{'status': 'M', 'path': 'src/app.py'}],
               'worker_result': {'kind': 'cli', 'skipped': list(skipped), 'status': worker_status,
                                 'answer': {'status': answer_status, 'summary': 'synthetic', 'open_questions': []},
                                 'model_identity': worker_mi or identity('codex-cli', None, SOL, source='claude',
                                                                         cli_default=True),
                                 'error': None},
               'checks': [{'command': 'python -m unittest -q', 'exit_code': 0 if checks_passed else 1,
                           'passed': checks_passed, 'timed_out': False, 'log_path': '@WORK@/check-1.log',
                           'tail': 'synthetic'}],
               'original_checkout_unchanged': unchanged, 'run_folder': '@WORK@/run'}
    if review:
        details['review'] = {'status': review_status, 'findings': [], 'report_path': '@WORK@/review.md',
                             'run_id': 'lrun_00000000000000000000000000000002',
                             'attempts': [attempt(0, identity('claude-cli', None, SONNET, source='claude',
                                                              cli_default=True), reviewer='claude-cli')]}
        if violations is not None:
            details['review']['violations'] = violations
    out = {'schema_version': '1.0', 'command': 'implement', 'execution_mode': 'live', 'run_id': None,
           'status': status, 'error_code': error_code, 'artifacts': [], 'details': details}
    return {'exit': exit, 'stdout': out}


def simple_resp(command, status, exit=0, error_code=None, details=None):
    return {'exit': exit, 'stdout': {'schema_version': '1.0', 'command': command, 'execution_mode': 'live',
                                     'run_id': None, 'status': status, 'error_code': error_code,
                                     'artifacts': [], 'details': details}}


class BridgeCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='hglos rg-o-')
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.assertFalse(self.tmp.is_relative_to(REPO))
        self.rundir = self.tmp / 'hglos' / 'validation' / 'rg-run'
        self.repo = self.tmp / 'repo'
        self.fakebin = self.tmp / 'fakebin'
        self.log = self.tmp / 'rglog'
        self.work = self.tmp / 'rgwork'
        self.scn = self.tmp / 'scenario.json'
        for p in (self.fakebin, self.log, self.work):
            p.mkdir()
        self.fake = self.fakebin / 'reviewgate.cmd'
        if os.name == 'nt':
            self.fake.write_text(f'@"{sys.executable}" -B "{FAKE}" %*\r\n@exit /b %ERRORLEVEL%\r\n',
                                 encoding='utf-8')
        else:
            self.fake = self.fakebin / 'reviewgate'
            self.fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" -B "{FAKE}" "$@"\n', encoding='utf-8')
            self.fake.chmod(0o755)
        self.make_repo()
        self.declare()
        self.instruction = self.tmp / 'instruction.md'
        self.instruction.write_bytes(INSTRUCTION.encode('utf-8'))
        self.scenario()

    # ------------------------------------------------------------ helpers

    def git(self, *args):
        hooks = self.tmp / 'nohooks'
        hooks.mkdir(exist_ok=True)
        subprocess.run(['git', '-c', 'user.name=example', '-c', 'user.email=example@example.invalid',
                        '-c', f'core.hooksPath={hooks}', '-c', 'commit.gpgsign=false',
                        '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', *args],
                       cwd=self.repo, check=True, capture_output=True)

    def make_repo(self):
        self.repo.mkdir()
        files = {'src/app.py': 'print("base")\n', 'src/util.py': 'print("util")\n',
                 'secret/key.txt': 'base\n', 'doc/plan.md': '# plan\n'}
        for rel, text in files.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding='utf-8')
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        (self.repo / 'src/app.py').write_text('print("base")\nSCOPED_CHANGE_MARKER = 1\n', encoding='utf-8')
        (self.repo / 'src/util.py').write_text('print("util")\nNOT_IN_INPUT_MARKER = 1\n', encoding='utf-8')
        (self.repo / 'src/new_file.py').write_text('UNTRACKED_CONTENT_MARKER = 1\n', encoding='utf-8')

    def declare(self, vendors=('anthropic', 'openai'), api_vendors=('openai',), raw=None, drop_api=False):
        path = self.repo / '.hglos/cross-host-egress.json'
        path.parent.mkdir(exist_ok=True)
        if raw is not None:
            path.write_text(raw, encoding='utf-8')
            return
        data = {'schema_version': 1, 'vendors': list(vendors), 'repo': 'repo',
                'exclude_globs': ['secret/**'], 'declared_by': 'example', 'date': '2026-11-06',
                'quote': 'example declaration quote'}
        if not drop_api:
            data['api_vendors'] = list(api_vendors) if isinstance(api_vendors, (list, tuple)) else api_vendors
        path.write_text(json.dumps(data), encoding='utf-8')

    def scenario(self, **commands):
        self.scn.write_text(json.dumps(commands, ensure_ascii=False), encoding='utf-8')

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k != 'REVIEWGATE_CLI' and not k.startswith('FAKE_RG_')}
        env.update(REVIEWGATE_CLI=str(self.fake), FAKE_RG_SCENARIO=str(self.scn), FAKE_RG_LOG=str(self.log),
                   FAKE_RG_WORK=str(self.work), FAKE_RG_MARK='mark-1', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def bridge(self, *args, env=None, script=BRIDGE, timeout=180):
        proc = subprocess.run([sys.executable, '-B', str(script), *map(str, args)], cwd=self.tmp,
                              env=env or self.env(), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)
        try:
            proc.result = json.loads(proc.stdout)
        except ValueError:
            self.fail(f'bridge stdout is not JSON: {proc.stdout!r} {proc.stderr!r}')
        self.assertEqual(proc.result.get('exit'), proc.returncode, proc.result)
        return proc

    def review(self, *reviewers, inputs=('src/app.py',), profile='code', impl=OPUS, extra=(), **kw):
        args = ['review', '--run-dir', self.rundir, '--repo', self.repo, '--instruction', self.instruction,
                '--profile', profile, '--implementer-model', impl]
        for r in reviewers or (f'codex-cli={SOL}',):
            args += ['--reviewer', r]
        for i in inputs:
            args += ['--input', i]
        return self.bridge(*args, *extra, **kw)

    def implement(self, worker='codex-cli', reviewers=('claude-cli',), inputs=(), extra=(), **kw):
        args = ['implement', '--run-dir', self.rundir, '--repo', self.repo, '--instruction', self.instruction,
                '--worker', worker]
        for r in reviewers:
            args += ['--reviewer', r]
        for i in inputs:
            args += ['--input', i]
        return self.bridge(*args, *extra, **kw)

    def reset(self):
        shutil.rmtree(self.rundir, ignore_errors=True)
        shutil.rmtree(self.log, ignore_errors=True)
        self.log.mkdir()

    def calls(self, command=None):
        items = [json.loads(p.read_text(encoding='utf-8')) for p in self.log.glob('call-*.json')]
        items.sort(key=lambda c: c['seq'])
        return [c for c in items if command is None or c['command'] == command]

    def callj(self, n=1):
        path = self.rundir / f'call_{n}.json'
        self.assertTrue(path.is_file(), f'missing {path}')
        return json.loads(path.read_text(encoding='utf-8'))

    def run_texts(self):
        if not self.rundir.exists():
            return {}
        return {p: p.read_bytes().decode('utf-8', 'replace') for p in self.rundir.rglob('*') if p.is_file()}

    def assert_cls(self, proc, classification, code, n=1):
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        self.assertEqual(proc.result['classification'], classification, proc.result)
        if n is not None:
            self.assertEqual(self.callj(n)['classification'], classification)

    def assert_refused(self, proc, classification):
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.result['classification'], classification, proc.result)
        self.assertEqual(self.calls(), [])
        self.assertEqual(sorted(p.name for p in self.rundir.glob('*')) if self.rundir.exists() else [], [])

    def assert_reason(self, record, prefix):
        self.assertIs(record['formal_round'], False, record)
        self.assertTrue(any(r.startswith(prefix) for r in record['formal_reasons']), record['formal_reasons'])

    def assert_not_formal(self, n=1):
        self.assertFalse((self.rundir / f'round_{n}.json').exists())
        self.assertFalse((self.rundir / f'reviewgate_{n}.raw.json').exists())

    def formal_scenario(self, n=2, model=SOL, variants=None, sessions=None, export=None, **kw):
        attempts, rollouts = codex_attempts(n, model=model, requested=model, variants=variants, sessions=sessions)
        return review_resp(attempts, rollouts, **kw), export or export_resp()


# ======================================================== condition 1: call contract

class CallContract(BridgeCase):
    def test_review_argv_cwd_env_and_records(self):
        rev, exp = self.formal_scenario()
        self.scenario(review=[rev], export=[exp])
        proc = self.review(inputs=('src/app.py', 'src/new_file.py'), extra=('--reference', 'doc/plan.md'))
        self.assert_cls(proc, 'ok', 0)
        call = self.calls('review')[0]
        argv = call['argv']
        self.assertEqual(argv[0], 'review')
        self.assertEqual([Path(p).resolve() for p in values(argv, '--prompt-file')],
                         [(self.rundir / 'request_1.md').resolve()])
        self.assertEqual([Path(p).resolve() for p in values(argv, '--source-root')], [self.repo])
        self.assertEqual([Path(p).resolve() for p in values(argv, '--repository-root')], [self.repo])
        self.assertEqual(values(argv, '--reviewer'), [f'codex-cli={SOL}'])
        self.assertEqual(values(argv, '--input'), ['src/app.py', 'src/new_file.py'])
        self.assertEqual(values(argv, '--reference'), ['doc/plan.md'])
        self.assertEqual(values(argv, '--profile'), ['code'])
        self.assertIn('--no-fallback', argv)
        self.assertNotIn('--prompt-stdin', argv)
        self.assertFalse([a for a in argv if 'SYNTHETIC-INSTRUCTION-MARKER' in a])
        self.assertEqual(Path(call['cwd']).resolve(), self.repo)
        self.assertEqual(call['mark'], 'mark-1')
        request = (self.rundir / 'request_1.md').read_bytes()
        self.assertTrue(request.startswith(INSTRUCTION.encode('utf-8')))
        self.assertEqual(call['prompt_sha256'], sha(request))
        self.assertEqual(sha((self.rundir / 'reviewgate_1.stdout.json').read_bytes()), call['stdout_sha256'])
        self.assertIn('SYNTHETIC-STDERR', (self.rundir / 'reviewgate_1.stderr.txt').read_text(encoding='utf-8'))
        record = self.callj()
        self.assertEqual(record['command'], 'review')
        self.assertEqual(record['argv'][0], '$REVIEWGATE_CLI')
        self.assertEqual(record['argv'][1:], argv)
        self.assertEqual(record['exit_code'], 0)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['run_id'], RUN_ID)
        self.assertEqual(record['reviewers_requested'], [SOL])
        for path, text in self.run_texts().items():
            self.assertNotIn(str(self.fakebin), text, path)
            self.assertNotIn(str(self.fake), text, path)

    def test_export_argv(self):
        rev, exp = self.formal_scenario()
        self.scenario(review=[rev], export=[exp])
        self.assert_cls(self.review(), 'ok', 0)
        argv = self.calls('export')[0]['argv']
        self.assertEqual(argv[:1], ['export'])
        self.assertEqual(values(argv, '--run'), [RUN_ID])
        self.assertEqual(values(argv, '--format'), ['hglos-findings'])
        self.assertNotIn('--allow-partial', argv)

    def test_implement_argv(self):
        self.scenario(implement=[impl_resp()])
        proc = self.implement(worker='codex-cli', reviewers=('claude-cli',), inputs=('src/app.py',),
                              extra=('--reference', 'doc/plan.md', '--check', 'python -m unittest -q',
                                     '--base', 'HEAD'))
        self.assert_cls(proc, 'ok', 0)
        call = self.calls('implement')[0]
        argv = call['argv']
        self.assertEqual(argv[0], 'implement')
        self.assertEqual([Path(p).resolve() for p in values(argv, '--prompt-file')],
                         [(self.rundir / 'request_1.md').resolve()])
        self.assertEqual([Path(p).resolve() for p in values(argv, '--repo')], [self.repo])
        self.assertEqual(values(argv, '--worker'), ['codex-cli'])
        self.assertEqual(values(argv, '--reviewer'), ['claude-cli'])
        self.assertEqual(values(argv, '--input'), ['src/app.py'])
        self.assertEqual(values(argv, '--reference'), ['doc/plan.md'])
        self.assertEqual(values(argv, '--check'), ['python -m unittest -q'])
        self.assertEqual(values(argv, '--base'), ['HEAD'])
        self.assertIn('--no-fallback', argv)
        for flag in ('--max-minutes', '--no-review', '--branch'):
            self.assertNotIn(flag, argv)
        self.assertEqual(Path(call['cwd']).resolve(), self.repo)
        record = self.callj()
        self.assertEqual(record['argv'][0], '$REVIEWGATE_CLI')
        self.assertEqual(record['worker_requested'], 'default:codex-cli')
        self.assertEqual(record['reviewers_requested'], ['default:claude-cli'])
        self.assertEqual(record['branch'], BRANCH)

    def test_implement_branch_continuation(self):
        self.scenario(implement=[impl_resp()])
        self.assert_cls(self.implement(extra=('--branch', BRANCH)), 'ok', 0)
        argv = self.calls('implement')[0]['argv']
        self.assertEqual(values(argv, '--branch'), [BRANCH])
        self.assertNotIn('--base', argv)

    def test_discard_and_status_argv(self):
        self.scenario(discard=[simple_resp('discard', 'discarded')], policy=[simple_resp('policy', 'ok')])
        proc = self.bridge('discard', '--run-dir', self.rundir, '--repo', self.repo, '--branch', BRANCH,
                           '--confirmed-by', 'example quote: discard it')
        self.assert_cls(proc, 'ok', 0, n=1)
        argv = self.calls('discard')[0]['argv']
        self.assertEqual(argv[0], 'discard')
        self.assertEqual([Path(p).resolve() for p in values(argv, '--repo')], [self.repo])
        self.assertEqual(values(argv, '--branch'), [BRANCH])
        self.assertEqual(self.callj(1)['confirmed_by'], 'example quote: discard it')
        proc = self.bridge('status', '--run-dir', self.rundir)
        self.assert_cls(proc, 'ok', 0, n=2)
        self.assertEqual(self.calls('policy')[0]['argv'], ['policy', 'status'])

    def test_provider_only_reviewer_is_recorded_as_default(self):
        attempts, rollouts = codex_attempts(1, requested=None, cli_default=True)
        self.scenario(review=[review_resp(attempts, rollouts)])
        self.assert_cls(self.review('codex-cli'), 'ok', 0)
        self.assertEqual(values(self.calls('review')[0]['argv'], '--reviewer'), ['codex-cli'])
        record = self.callj()
        self.assertEqual(record['reviewers_requested'], ['default:codex-cli'])
        self.assertIn('요청 모델 default:codex-cli', record['header'])

    def test_records_are_never_overwritten(self):
        self.rundir.mkdir(parents=True)
        (self.rundir / 'request_1.md').write_bytes(b'STRAY')
        rev, exp = self.formal_scenario()
        self.scenario(review=[rev], export=[exp])
        proc = self.review()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.result['classification'], 'contract')
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.rundir / 'request_1.md').read_bytes(), b'STRAY')

    def test_second_call_gets_next_number_and_first_stays(self):
        attempts, rollouts = codex_attempts(1, requested=None, cli_default=True)
        a2, r2 = codex_attempts(1, requested=None, cli_default=True, sessions={0: tid(7)})
        self.scenario(review=[review_resp(attempts, rollouts), review_resp(a2, r2)])
        self.assert_cls(self.review('codex-cli'), 'ok', 0, n=1)
        first = (self.rundir / 'call_1.json').read_bytes()
        proc = self.review('codex-cli')
        self.assert_cls(proc, 'ok', 0, n=2)
        self.assertEqual(proc.result['round'], 2)
        self.assertEqual((self.rundir / 'call_1.json').read_bytes(), first)
        self.assertTrue((self.rundir / 'request_2.md').is_file())

    def test_review_requires_input(self):
        self.assert_refused(self.review(inputs=()), 'input_required')

    def test_api_provider_requires_model(self):
        for spec in ('openai', 'together'):
            with self.subTest(spec=spec):
                self.assert_refused(self.review(spec), 'model_required')

    def test_diff_base_attaches_diff_and_untracked_names(self):
        attempts, rollouts = codex_attempts(1, requested=None, cli_default=True)
        self.scenario(review=[review_resp(attempts, rollouts)])
        self.assert_cls(self.review('codex-cli', inputs=('src/app.py', 'src/new_file.py'),
                                    extra=('--diff-base', 'HEAD')), 'ok', 0)
        request = (self.rundir / 'request_1.md').read_text(encoding='utf-8')
        self.assertTrue(request.startswith(INSTRUCTION))
        self.assertIn('+SCOPED_CHANGE_MARKER = 1', request)
        self.assertIn('src/new_file.py', request)
        self.assertNotIn('UNTRACKED_CONTENT_MARKER', request)
        self.assertNotIn('NOT_IN_INPUT_MARKER', request)


# ======================================================== condition 1: classification table (6.1)

class Classification(BridgeCase):
    def test_review_partial_is_reference_with_identity(self):
        attempts, rollouts = codex_attempts(2)
        self.scenario(review=[review_resp(attempts, rollouts, status='partial', error_code='partial_review',
                                          exit=20)], export=[export_resp()])
        proc = self.review()
        self.assert_cls(proc, 'review_partial', 1)
        record = self.callj()
        self.assertEqual(record['model_check'], '일치')
        self.assertEqual([a['model_check'] for a in record['attempts']], ['일치', '일치'])
        self.assertIn('정식 회차 아님', record['header'])
        self.assert_reason(record, 'not_ok:review_partial')
        self.assertEqual(self.calls('export'), [])
        self.assert_not_formal()

    def test_review_failed_partial_review_without_valid_units(self):
        self.scenario(review=[review_resp([], status='failed', error_code='partial_review', exit=20)])
        self.assert_cls(self.review(), 'failed', 1)
        self.assertEqual(len(self.calls('review')), 1)
        self.assert_not_formal()

    def test_exit_codes_and_error_codes(self):
        cases = [
            (dict(exit=13, status='failed', error_code='state_store_busy'), 'reviewgate_busy', 1),
            (dict(exit=13, status='needs_reconciliation', error_code='needs_reconciliation'),
             'needs_reconciliation', 1),
            (dict(exit=2, status='failed', error_code='unknown_model'), 'contract', 2),
            (dict(exit=2, status='failed', error_code='instruction_required'), 'contract', 2),
            (dict(exit=12, status='failed', error_code='request_context_exceeded'), 'input_too_large', 2),
            (dict(exit=2, status='failed', error_code='instruction_limit'), 'input_too_large', 2),
            (dict(exit=0, status='queued', error_code=None), 'contract', 2),
        ]
        for kw, classification, code in cases:
            with self.subTest(**kw):
                self.reset()
                self.scenario(review=[review_resp([], **kw)], export=[export_resp()])
                proc = self.review()
                self.assert_cls(proc, classification, code)
                self.assertEqual(len(self.calls('review')), 1)
                self.assertEqual(self.calls('export'), [])
                self.assert_not_formal()

    def test_stdout_contract(self):
        cases = [{'exit': 0, 'stdout_text': 'not json'}, {'exit': 0, 'stdout': [1, 2]},
                 {'exit': 0, 'stdout': {'command': 'review', 'details': {}}},
                 {'exit': 0, 'stdout_text': '\ufeff' + json.dumps({'status': 'completed'})}]
        for resp in cases:
            with self.subTest(resp=resp):
                self.reset()
                self.scenario(review=[resp])
                self.assert_cls(self.review(), 'contract', 2)
                call = self.calls('review')[0]
                self.assertEqual(sha((self.rundir / 'reviewgate_1.stdout.json').read_bytes()),
                                 call['stdout_sha256'])

    def test_approval_required_exit_10(self):
        violations = [{'code': 'reviewer_not_allowed', 'message': 'synthetic violation text'}]
        out = review_resp([], status='approval_required', error_code='policy_violation', exit=10)
        out['stdout']['details']['violations'] = violations
        self.scenario(review=[out])
        self.assert_cls(self.review(), 'approval_required', 3)
        self.assertEqual(self.callj()['violations'], violations)
        self.assertEqual(len(self.calls()), 1)

    def test_implement_nested_approval_required(self):
        violations = [{'code': 'reviewer_not_allowed', 'message': 'synthetic nested violation'}]
        self.scenario(implement=[impl_resp(review_status='approval_required', violations=violations)])
        self.assert_cls(self.implement(), 'approval_required', 3)
        self.assertEqual(self.callj()['violations'], violations)

    def test_implement_checkout_changed(self):
        self.scenario(implement=[impl_resp(unchanged=False)])
        self.assert_cls(self.implement(), 'checkout_changed', 3)
        self.assertEqual(self.callj()['branch'], BRANCH)

    def test_implement_checks_failed(self):
        self.scenario(implement=[impl_resp(checks_passed=False)])
        self.assert_cls(self.implement(), 'checks_failed', 1)
        record = self.callj()
        self.assertEqual(record['branch'], BRANCH)
        self.assertIn('check-1.log', json.dumps(record['checks'], ensure_ascii=False))

    def test_implement_partial_variants(self):
        cases = [dict(worker_status='unknown'), dict(answer_status='partial'), dict(skipped=['big.bin']),
                 dict(review_status='failed'), dict(review=False)]
        for kw in cases:
            with self.subTest(**kw):
                self.reset()
                self.scenario(implement=[impl_resp(**kw)])
                self.assert_cls(self.implement(), 'partial', 1)
                self.assertEqual(self.callj()['branch'], BRANCH)

    def test_implement_no_changes_is_ok_and_worker_failed_is_failed(self):
        self.scenario(implement=[impl_resp(status='no_changes', review=False),
                                 impl_resp(status='worker_failed', exit=20, error_code='worker_failed')])
        self.assert_cls(self.implement(), 'ok', 0, n=1)
        self.assert_cls(self.implement(), 'failed', 1, n=2)

    def test_implement_review_is_reference_only(self):
        self.scenario(implement=[impl_resp()], export=[export_resp()])
        self.assert_cls(self.implement(), 'ok', 0)
        self.assertEqual(self.calls('export'), [])
        self.assertFalse(list(self.rundir.glob('round_*.json')))

    def test_timeout_kills_tree(self):
        resp = review_resp([])
        resp.update(sleep=60, grandchild=True)
        self.scenario(review=[resp])
        started = time.monotonic()
        proc = self.review(extra=('--timeout', '4'))
        self.assertLess(time.monotonic() - started, 45)
        self.assert_cls(proc, 'timeout', 1)
        pid = self.calls('review')[0]['grandchild_pid']
        deadline = time.monotonic() + 10
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(pid_alive(pid))
        self.assertEqual(len(self.calls('review')), 1)


# ======================================================== condition 2: downgrade

class Downgrade(BridgeCase):
    def test_env_missing_or_file_missing(self):
        for value in (None, str(self.tmp / 'missing' / 'reviewgate.exe')):
            with self.subTest(value=value):
                self.reset()
                proc = self.review(env=self.env(REVIEWGATE_CLI=value))
                self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
                self.assertEqual(proc.result['classification'], 'reviewgate_unavailable')
                record = self.callj()
                self.assertEqual(record['classification'], 'reviewgate_unavailable')
                self.assertEqual(record['reason'], 'not_installed')
                self.assertEqual(self.calls(), [])

    def test_no_path_lookup(self):
        shim = self.tmp / 'onpath'
        shim.mkdir()
        shutil.copy(self.fake, shim / self.fake.name)
        env = self.env(REVIEWGATE_CLI=None)
        env['PATH'] = str(shim) + os.pathsep + env.get('PATH', '')
        proc = self.review(env=env)
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(self.calls(), [])

    def test_exit_11_reasons_and_no_retry(self):
        for code in ('cli_not_found', 'credential_missing'):
            with self.subTest(code=code):
                self.reset()
                self.scenario(review=[review_resp([], status='failed', error_code=code, exit=11)])
                proc = self.review()
                self.assert_cls(proc, 'reviewgate_unavailable', 4)
                self.assertEqual(self.callj()['reason'], code)
                self.assertEqual(len(self.calls()), 1)
                self.assertTrue((self.rundir / 'reviewgate_1.stdout.json').is_file())


# ======================================================== condition 3: approval boundary

class ApprovalBoundary(BridgeCase):
    def test_discard_needs_confirmation(self):
        self.scenario(discard=[simple_resp('discard', 'discarded')])
        for extra in ((), ('--confirmed-by', '   ')):
            with self.subTest(extra=extra):
                proc = self.bridge('discard', '--run-dir', self.rundir, '--repo', self.repo, '--branch', BRANCH,
                                   *extra)
                self.assert_refused(proc, 'confirmation_required')


# ======================================================== condition 4: attempt identity (6.2)

class AttemptIdentity(BridgeCase):
    def check(self, spec, attempts, expected, rollouts=(), impl=OPUS):
        self.reset()
        self.scenario(review=[review_resp(attempts, rollouts)], export=[export_resp()])
        proc = self.review(spec, impl=impl)
        self.assertIn(proc.returncode, (0, 2), proc.stdout + proc.stderr)
        record = self.callj()
        self.assertEqual([a['model_check'] for a in record['attempts']], expected, record['attempts'])
        return record

    def test_missing_identity_source_or_observed(self):
        base = identity('codex-cli', SOL, SOL, k=0)
        no_source = dict(base, source=None)
        empty = dict(base, model_observed=[])
        for mi in (None, no_source, empty):
            with self.subTest(mi=mi is None and 'none' or sorted(mi)):
                record = self.check(f'codex-cli={SOL}', [attempt(0, mi)], ['로그 없음'],
                                    rollouts=[{'session': tid(1), 'model': SOL}])
                self.assert_reason(record, 'model_check:로그 없음')

    def test_provider_only_is_default_whatever_reviewgate_says(self):
        for requested, cli_default in ((None, True), (SOL, False)):
            with self.subTest(requested=requested):
                attempts, rollouts = codex_attempts(1, requested=requested, cli_default=cli_default)
                record = self.check('codex-cli', attempts, [DEFAULT], rollouts)
                self.assertEqual(record['model_check'], DEFAULT)
                self.assertIn('순위: 미확인(로그 없음', record['header'])

    def test_request_delivery_mismatch(self):
        for requested, cli_default in ((TERRA, False), (None, True), (SOL, True)):
            with self.subTest(requested=requested, cli_default=cli_default):
                attempts, rollouts = codex_attempts(1, requested=SOL)
                attempts[0]['model_identity']['model_requested'] = requested
                if cli_default:
                    attempts[0]['model_identity']['cli_default_model'] = True
                self.check(f'codex-cli={SOL}', attempts, ['불일치'], rollouts)

    def test_claude_final_missing_or_mismatch(self):
        mi = identity('claude-cli', OPUS, None, observed=[OPUS], source='claude')
        self.check(f'claude-cli={OPUS}', [attempt(0, mi, 'claude-cli')], ['최종 작성자 미확인'], impl=SOL)
        mi = identity('claude-cli', OPUS, SONNET, observed=[OPUS, SONNET], source='claude')
        self.check(f'claude-cli={OPUS}', [attempt(0, mi, 'claude-cli')], ['불일치'], impl=SOL)
        mi = identity('claude-cli', OPUS, OPUS, observed=[OPUS, HAIKU], source='claude')
        self.check(f'claude-cli={OPUS}', [attempt(0, mi, 'claude-cli')], ['일치'], impl=SOL)

    def test_codex_one_observed_value_differs(self):
        attempts, rollouts = codex_attempts(1)
        attempts[0]['model_identity']['model_observed'] = [SOL, TERRA]
        self.check(f'codex-cli={SOL}', attempts, ['불일치'], rollouts)

    def test_api_reviewer_match(self):
        mi = identity('openai', LUNA, LUNA, source='api')
        self.check(f'openai={LUNA}', [attempt(0, mi, 'openai')], ['일치'])


class RoundIdentity(BridgeCase):
    def run_round(self, checks):
        attempts, rollouts = codex_attempts(len(checks))
        for k, kind in enumerate(checks):
            mi = attempts[k]['model_identity']
            if kind == 'mismatch':
                mi['model_observed'] = [SOL, TERRA]
            elif kind == 'nolog':
                mi['source'] = None
        self.scenario(review=[review_resp(attempts, rollouts)], export=[export_resp()])
        self.review()
        return self.callj()

    def test_round_aggregation(self):
        cases = [(['ok'], '일치'), (['ok', 'ok', 'ok'], '일치'), (['ok', 'mismatch', 'ok'], '불일치'),
                 (['ok', 'nolog'], '로그 없음'), (['nolog', 'mismatch'], '불일치')]
        for checks, expected in cases:
            with self.subTest(checks=checks):
                self.reset()
                self.assertEqual(self.run_round(checks)['model_check'], expected)

    def test_empty_attempts(self):
        self.scenario(review=[review_resp([])], export=[export_resp()])
        self.review()
        record = self.callj()
        self.assertEqual(record['model_check'], '로그 없음')
        self.assert_reason(record, 'model_check:로그 없음')
        self.assert_not_formal()


# ======================================================== condition 4: Codex rollout F3 (D11)

class RolloutF3(BridgeCase):
    def test_variants_fail_f3(self):
        for variant in ('missing', 'wrong_name', 'context_model', 'bad_injection', 'memory', 'sent_mismatch',
                        'instr_missing', 'instr_two', 'instr_diff'):
            with self.subTest(variant=variant):
                self.reset()
                rev, exp = self.formal_scenario(variants={1: variant})
                self.scenario(review=[rev], export=[exp])
                self.review()
                record = self.callj()
                self.assertEqual([a['f3']['ok'] for a in record['attempts']], [True, False])
                self.assert_reason(record, 'f3')
                self.assert_not_formal()

    def test_rollout_body_is_not_recorded(self):
        rev, exp = self.formal_scenario()
        self.scenario(review=[rev], export=[exp])
        self.assert_cls(self.review(), 'ok', 0)
        for path, text in self.run_texts().items():
            self.assertNotIn('SYNTHETIC-ROLLOUT-BODY', text, path)
            self.assertNotIn('SYNTHETIC SYSTEM TEXT', text, path)


# ======================================================== condition 4: formal round conditions

class FormalRound(BridgeCase):
    def test_codex_formal_round(self):
        rev, exp = self.formal_scenario(n=2)
        self.scenario(review=[rev], export=[exp])
        proc = self.review()
        self.assert_cls(proc, 'ok', 0)
        record = self.callj()
        self.assertIs(record['formal_round'], True, record.get('formal_reasons'))
        self.assertEqual(record['model_check'], '일치')
        self.assertEqual(record['model_observed'], SOL)
        self.assertEqual(record['rank'], 1)
        self.assertIs(record['integrity']['changed'], False)
        self.assertEqual([a['f3']['ok'] for a in record['attempts']], [True, True])
        export_file = next(self.work.glob('exports/*/hglos.findings.live.json'))
        self.assertEqual((self.rundir / 'reviewgate_1.raw.json').read_bytes(), export_file.read_bytes())
        round_json = json.loads((self.rundir / 'round_1.json').read_text(encoding='utf-8'))
        self.assertEqual(round_json['contract_violations'], [])
        self.assertEqual([f['fp'] for f in round_json['findings']], ['src/app.py:2:logic'])
        for text in ('판정: 일치', '순위: 1', f'요청 모델 {SOL}', f'관측 모델 {SOL}', 'turn_context.payload.model',
                     '관측 출처: CLI 로컬 기록', '구현자와 같은 패밀리: 아니오'):
            self.assertIn(text, record['header'])
        self.assertNotIn('정식 회차 아님', record['header'])

    def test_run_dir_inside_target_repo(self):
        # Shape of the real call (I-1): the run folder is <HGLOS>/validation/<run> and HGLOS is the target.
        self.rundir = self.repo / 'validation' / 'rg-run'
        rev, exp = self.formal_scenario(n=1)
        self.scenario(review=[rev], export=[exp])
        self.assert_cls(self.review(), 'ok', 0)
        record = self.callj()
        self.assertIs(record['integrity']['changed'], False)
        self.assertIs(record['formal_round'], True, record.get('formal_reasons'))
        self.assertTrue((self.rundir / 'round_1.json').is_file())

    def test_claude_formal_round_without_f3(self):
        mi = identity('claude-cli', OPUS, OPUS, source='claude')
        self.scenario(review=[review_resp([attempt(0, mi, 'claude-cli')])], export=[export_resp()])
        self.assert_cls(self.review(f'claude-cli={OPUS}', impl=SOL), 'ok', 0)
        record = self.callj()
        self.assertIs(record['formal_round'], True, record.get('formal_reasons'))
        self.assertIn('stream 미보존', record['header'])
        self.assertTrue((self.rundir / 'round_1.json').is_file())

    def test_not_candidates_never_export(self):
        two = codex_attempts(1)
        cases = [((f'codex-cli={SOL}', f'claude-cli={OPUS}'), 'code', two),
                 (('codex-cli',), 'code', codex_attempts(1, requested=None, cli_default=True)),
                 ((f'codex-cli={SOL}',), 'document', codex_attempts(1))]
        for reviewers, profile, (attempts, rollouts) in cases:
            with self.subTest(reviewers=reviewers, profile=profile):
                self.reset()
                self.scenario(review=[review_resp(attempts, rollouts)], export=[export_resp()])
                self.assert_cls(self.review(*reviewers, profile=profile), 'ok', 0)
                self.assertEqual(self.calls('export'), [])
                self.assert_reason(self.callj(), 'not_candidate')
                self.assert_not_formal()

    def test_excluded_rank(self):
        mi = identity('claude-cli', HAIKU, HAIKU, source='claude')
        self.scenario(review=[review_resp([attempt(0, mi, 'claude-cli')])], export=[export_resp()])
        self.review(f'claude-cli={HAIKU}', impl=SOL)
        record = self.callj()
        self.assertEqual(record['model_check'], '일치')
        self.assert_reason(record, 'rank_excluded')
        self.assert_not_formal()

    def test_unknown_grade_stays_formal(self):
        rev, exp = self.formal_scenario(n=1, model=TERRA)
        self.scenario(review=[rev], export=[exp])
        self.assert_cls(self.review(f'codex-cli={TERRA}', impl=SOL), 'ok', 0)
        record = self.callj()
        self.assertIs(record['formal_round'], True, record.get('formal_reasons'))
        self.assertIn('순위: 미확인(등급 미확인)', record['header'])

    def test_model_change_in_run(self):
        r1, e1 = self.formal_scenario(n=1)
        a2, ro2 = codex_attempts(1, model=TERRA, requested=TERRA, sessions={0: tid(5)})
        self.scenario(review=[r1, review_resp(a2, ro2)], export=[e1, export_resp()])
        self.assert_cls(self.review(), 'ok', 0, n=1)
        self.assertIs(self.callj(1)['formal_round'], True)
        self.review(f'codex-cli={TERRA}')
        self.assert_reason(self.callj(2), 'model_changed')
        self.assert_not_formal(2)

    def test_run_stops_after_non_formal_review(self):
        a1, ro1 = codex_attempts(1)
        a1[0]['model_identity']['model_observed'] = [SOL, TERRA]
        r2, e2 = self.formal_scenario(n=1, sessions={0: tid(5)})
        self.scenario(review=[review_resp(a1, ro1), r2], export=[export_resp(), e2])
        self.review()
        self.assertIs(self.callj(1)['formal_round'], False)
        self.review()
        self.assert_reason(self.callj(2), 'run_stopped')
        self.assert_not_formal(2)

    def test_previous_non_review_call_does_not_matter(self):
        self.scenario(policy=[simple_resp('policy', 'ok')])
        self.assert_cls(self.bridge('status', '--run-dir', self.rundir), 'ok', 0, n=1)
        rev, exp = self.formal_scenario(n=1)
        self.scenario(review=[rev], export=[exp])
        self.assert_cls(self.review(), 'ok', 0, n=2)
        self.assertIs(self.callj(2)['formal_round'], True, self.callj(2).get('formal_reasons'))
        self.assertTrue((self.rundir / 'round_2.json').is_file())

    def test_session_id_reuse_in_round_and_across_rounds(self):
        rev, exp = self.formal_scenario(n=2, sessions={0: tid(3), 1: tid(3)})
        self.scenario(review=[rev], export=[exp])
        self.review()
        self.assert_reason(self.callj(1), 'session_id')
        self.reset()
        r1, e1 = self.formal_scenario(n=1, sessions={0: tid(3)})
        r2, e2 = self.formal_scenario(n=1, sessions={0: tid(3)})
        self.scenario(review=[r1, r2], export=[e1, e2])
        self.review()
        self.assertIs(self.callj(1)['formal_round'], True, self.callj(1).get('formal_reasons'))
        self.review()
        self.assert_reason(self.callj(2), 'session_id')
        self.assert_not_formal(2)

    def test_fallback_and_resumed(self):
        for where in ('attempt', 'identity', 'resumed'):
            with self.subTest(where=where):
                self.reset()
                attempts, rollouts = codex_attempts(1)
                if where == 'attempt':
                    attempts[0]['fallback_used'] = True
                elif where == 'identity':
                    attempts[0]['model_identity']['fallback_used'] = True
                else:
                    attempts[0]['model_identity']['resumed'] = True
                self.scenario(review=[review_resp(attempts, rollouts)], export=[export_resp()])
                self.review()
                self.assert_reason(self.callj(), 'resumed' if where == 'resumed' else 'fallback')
                self.assert_not_formal()

    def test_request_sha256_mismatch(self):
        for where in ('attempt', 'details'):
            with self.subTest(where=where):
                self.reset()
                attempts, rollouts = codex_attempts(1)
                kw = {}
                if where == 'attempt':
                    attempts[0]['model_identity']['request_sha256'] = '0' * 64
                else:
                    kw['request_sha'] = '0' * 64
                self.scenario(review=[review_resp(attempts, rollouts, **kw)], export=[export_resp()])
                self.review()
                self.assert_reason(self.callj(), 'request_sha256')
                self.assert_not_formal()

    def test_integrity_change(self):
        rev, exp = self.formal_scenario(n=1, touch='src/app.py')
        self.scenario(review=[rev], export=[exp])
        self.review()
        record = self.callj()
        self.assertIs(record['integrity']['changed'], True)
        self.assert_reason(record, 'integrity')
        self.assert_not_formal()

    def test_round_tools_missing(self):
        bundle = self.tmp / 'bundle' / 'scripts'
        bundle.mkdir(parents=True)
        shutil.copy(BRIDGE, bundle / BRIDGE.name)
        shutil.copy(CROSS_HOST, bundle / CROSS_HOST.name)
        rev, exp = self.formal_scenario(n=1)
        self.scenario(review=[rev], export=[exp])
        self.review(script=bundle / BRIDGE.name)
        self.assert_reason(self.callj(), 'round_tools_missing')
        self.assertFalse((self.rundir / 'round_1.json').exists())

    def test_findings_contract_failures(self):
        bad_schema = {'findings': [{'path': 'src/app.py', 'line': 2, 'category': 'logic', 'severity': 'low',
                                    'evidence': 'observed', 'claim': 'missing detail'}]}
        duplicate = {'findings': VALID_FINDINGS['findings'] * 2}
        for name, content in (('schema', bad_schema), ('duplicate', duplicate)):
            with self.subTest(name=name):
                self.reset()
                rev, exp = self.formal_scenario(n=1, export=export_resp(content=content))
                self.scenario(review=[rev], export=[exp])
                proc = self.review()
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assert_reason(self.callj(), 'contract')
                round_file = self.rundir / 'round_1.json'
                if round_file.exists():
                    data = json.loads(round_file.read_text(encoding='utf-8'))
                    self.assertTrue(data.get('codex_invoked') is False or data.get('contract_violations'), data)


# ======================================================== condition 4: export (S5)

class Export(BridgeCase):
    def run_export(self, exp):
        self.reset()
        rev, _ = self.formal_scenario(n=1)
        self.scenario(review=[rev], export=[exp])
        proc = self.review()
        return proc, self.callj()

    def test_incomplete_export_keeps_ok(self):
        for code, error in ((20, 'projection_incomplete'), (13, 'export_not_ready'),
                            (14, 'hglos_code_profile_required')):
            with self.subTest(code=code):
                proc, record = self.run_export(export_resp(exit=code, status='failed', error_code=error,
                                                           artifacts=[]))
                self.assert_cls(proc, 'ok', 0)
                self.assert_reason(record, f'export_incomplete:{error}')
                self.assertEqual(record['export']['exit_code'], code)
                self.assert_not_formal()

    def test_export_contract(self):
        good = export_resp()['stdout']['artifacts'][0]
        cases = {
            'exit 2': export_resp(exit=2, status='failed', error_code='invalid_arguments', artifacts=[]),
            'exit 21': export_resp(exit=21, status='failed', error_code='synthetic', artifacts=[]),
            'no item': export_resp(artifacts=[dict(good, path=f'exports/{RUN_ID}/hglos.findings.partial.live.json')]),
            'no absolute_path': export_resp(artifacts=[{k: v for k, v in good.items() if k != 'absolute_path'}]),
            'sha256': export_resp(artifacts=[dict(good, sha256='0' * 64)]),
            'bytes': export_resp(artifacts=[dict(good, bytes=1)]),
            'not json': {'exit': 0, 'stdout_text': 'not json', 'export_file': {'content': VALID_FINDINGS}},
        }
        for name, exp in cases.items():
            with self.subTest(name=name):
                proc, record = self.run_export(exp)
                self.assert_cls(proc, 'contract', 2)
                self.assertIs(record['formal_round'], False)
                self.assert_not_formal()


# ======================================================== condition 5: egress

class Egress(BridgeCase):
    def test_declaration_missing(self):
        (self.repo / '.hglos/cross-host-egress.json').unlink()
        self.assert_refused(self.review(), 'egress_refused')

    def test_reviewer_vendor_missing(self):
        self.declare(vendors=('anthropic',))
        self.assert_refused(self.review(), 'egress_refused')

    def test_worker_vendor_missing(self):
        self.declare(vendors=('anthropic',))
        self.assert_refused(self.implement(worker='codex-cli', reviewers=('claude-cli',)), 'egress_refused')

    def test_api_vendors(self):
        for kw in (dict(drop_api=True), dict(api_vendors='openai'), dict(api_vendors=('',)),
                   dict(api_vendors=())):
            with self.subTest(**kw):
                self.declare(**kw)
                self.assert_refused(self.review(f'openai={LUNA}'), 'egress_refused')
        self.declare()
        mi = identity('openai', LUNA, LUNA, source='api')
        self.scenario(review=[review_resp([attempt(0, mi, 'openai')])], export=[export_resp()])
        proc = self.review(f'openai={LUNA}')
        self.assertEqual(len(self.calls('review')), 1, proc.stdout + proc.stderr)

    def test_excluded_inputs(self):
        cases = [dict(inputs=('secret',)), dict(inputs=('secret/key.txt',)),
                 dict(inputs=('src/app.py',), extra=('--reference', 'secret/key.txt'))]
        for kw in cases:
            with self.subTest(**kw):
                self.assert_refused(self.review(**kw), 'egress_refused')

    def test_on_hold_providers(self):
        for spec in ('agy-cli', 'google=gemini-3.8-flash'):
            with self.subTest(spec=spec):
                self.assert_refused(self.review(spec), 'provider_on_hold')
        self.assert_refused(self.implement(worker='agy-cli'), 'provider_on_hold')

    def test_implement_inputs_and_vendors(self):
        self.assert_refused(self.implement(worker=f'openai={LUNA}'), 'input_required')
        self.assert_refused(self.implement(worker='codex-cli', reviewers=(f'openai={LUNA}',),
                                           inputs=('src/app.py',)), 'same_vendor')
        self.assert_refused(self.implement(worker='codex-cli', reviewers=('codex-cli',)), 'same_vendor')
        proc = self.implement(reviewers=())
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_api_worker_and_identity_record(self):
        mi = identity('openai', LUNA, LUNA, source='api')
        self.scenario(implement=[impl_resp(worker_mi=mi)])
        self.assert_cls(self.implement(worker=f'openai={LUNA}', inputs=('src/app.py',)), 'ok', 0)
        record = self.callj()
        self.assertEqual(record['worker_requested'], LUNA)
        self.assertEqual(record['worker_model_check'], '일치')


# ======================================================== conditions 3 and 6: static checks

class StaticSource(unittest.TestCase):
    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')
        self.tree = ast.parse(self.source)
        self.strings = [n.value for n in ast.walk(self.tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    def test_no_forbidden_commands(self):
        forbidden = {'set', 'set-implement', 'revoke', 'credential', 'grant', 'merge', 'cherry-pick',
                     '--allow-partial'}
        self.assertEqual(sorted(forbidden & set(self.strings)), [])
        joined = '\n'.join(self.strings)
        for phrase in ('policy set', 'git merge', 'cherry-pick', 'allow-partial', 'policy revoke'):
            self.assertNotIn(phrase, joined)

    def test_no_http_or_key_reading(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
                imported |= {f'{node.module}.{a.name}' for a in node.names}
        for name in ('urllib.request', 'http.client', 'requests', 'httpx', 'urllib3', 'aiohttp', 'socket'):
            self.assertFalse([m for m in imported if m == name or m.startswith(name + '.')], name)
        self.assertFalse([s for s in self.strings if 'API_KEY' in s.upper()])
        env_names = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                fn = ast.unparse(node.func)
                if fn in ('os.environ.get', 'os.getenv', 'environ.get', 'getenv') and node.args:
                    env_names.append(ast.unparse(node.args[0]))
            elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in ('os.environ', 'environ'):
                env_names.append(ast.unparse(node.slice))
        self.assertEqual(sorted(set(env_names)), ["'REVIEWGATE_CLI'"])
        for word in ('price', 'pricing', 'per_million', 'usd_per'):
            self.assertNotIn(word, self.source.lower())

    def test_no_shell_and_no_state_folder(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == 'shell':
                        self.assertFalse(isinstance(kw.value, ast.Constant) and kw.value.value is True)
                self.assertNotIn(ast.unparse(node.func), ('os.system', 'os.popen', 'subprocess.getoutput',
                                                          'subprocess.getstatusoutput'))
        self.assertFalse([s for s in self.strings if '.reviewgate' in s.lower()])

    def test_reuses_cross_host(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
        self.assertIn('cross_host', imported)
        for name in ('def load_egress', 'def rank_status', 'def message_text', 'def validate_findings',
                     'def make_round'):
            self.assertNotIn(name, self.source)


if __name__ == '__main__':
    unittest.main()
