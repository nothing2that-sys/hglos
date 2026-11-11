"""doc/33 O oracle candidate - NOT FROZEN until JH confirms this file and the
fixtures under tests/fixtures/cross_host/ by SHA-256 (U13).

Covers doc/33 section 2 conditions 1-5 and 7 plus the 6.1, 6.3 and 6.4 rules,
using fake CLIs only. No real codex/claude/agy is started and nothing leaves
the machine. All identifiers are synthetic (U13): UUIDs only
00000000-0000-4000-8000-0000000000NN, home paths only C:\\Users\\example,
account/installation IDs only with the example- prefix.

Public interface proposed here (plugin/hglos/scripts/cross_host.py, stdlib only):
  call  --repo R --run RUN --round N --tool codex|claude|agy --kind review|context
        --instruction FILE [--scope REL]... [--canon REL]... [--implementer-model M]
        [--model ID] [--effort E] [--timeout SEC] [--downgraded-from RUN]
  usage [--tools codex,agy,claude] [--claude-usage-note TEXT]    -> stdout JSON, writes nothing
  route --usage FILE --implementer-model M --main codex|claude --kind review|context
                                                                  -> stdout JSON, writes nothing
  clean --run RUN                    -> removes only %TEMP%/hglos-cross-host/<RUN>
CLI program per tool: HGLOS_CROSS_HOST_<CODEX|CLAUDE|AGY>_CMD = JSON argv prefix,
otherwise the tool name on PATH. Run folder <repo>/validation/<RUN>/; temporary
folder tempfile.gettempdir()/hglos-cross-host/<RUN>/. Codex home = CODEX_HOME or ~/.codex.
Egress declaration <repo>/.hglos/cross-host-egress.json:
  {"schema_version": 1, "vendors": [...], "repo": str, "exclude_globs": [...],
   "account_data": {...}, "declared_by": str, "date": str, "quote": str}
  repo must equal the --repo folder name; declared_by and quote must be non-empty.
  vendor per tool: codex -> openai, claude -> anthropic, agy -> google; agy with a
  claude-* model needs both google and anthropic (U13 D4).
usage JSON: {"tools": {<tool>: {"status": "ok"|"unknown", "data": <allowed fields|null>,
  ...}}}; codex data keys = planType, primary, secondary, rateLimitReachedType
  (+ usedPercent, windowDurationMins, resetsAt inside windows); agy data = groups[name,
  buckets[window, remaining_fraction, reset_time]]. agy unknown records tokens_consumed.
  claude: status unknown, alternatives (non-empty list), note.
  call writes usage-before_<N>.json / usage-after_<N>.json in the same shape for the
  called tool; usage-after marks "exhausted": true on a limit failure.
route JSON: order (route ids in 6.2 table order, except Claude implementer + Codex main:
  native-codex, codex-exec, handoff per HOST.md native-first rule, U13 D1), selected, selected_model,
  skipped [{route, reason: "below_threshold"}], limit_unknown (for selected).
  Every route in order is evaluated against usage, also routes after the selected
  one, so skipped lists every below-threshold route in table order. native-*,
  handoff and unknown usage are never skipped (unknown -> limit_unknown true).
  route ids: claude-p, agy-claude, codex-exec, agy-gemini, native-codex, native-claude, handoff.
  Threshold per tool/model group: codex primary or secondary usedPercent > 90; agy group
  (Gemini Models -> gemini flash, Gemini 4 Argon -> argon, Claude and GPT models ->
  claude-*) any bucket remaining_fraction < 0.10. Argon is chosen only when its own
  group passes, otherwise gemini-3.8-flash-*.
Never-used models: agy gpt-oss-* (any kind) and agy gemini-* for review are refused
  before any CLI call (3, U11, U16). Excluded-by-rank Claude models (Sonnet for an Opus
  implementer) are called and then fail as identity_unconfirmed (condition 4).
Request: request_<N>.md = --instruction text + scoped git diff + untracked scoped file
  list + canon paths + exclude globs (excluded files' diff is not included). Its size
  overhead must not depend on the instruction size, run id or instruction file name.
  codex/claude: request on stdin. agy: -p string = request_<N>.md; review material
  (diff, copies of scoped files minus exclude globs) goes into the packet folder.
input_checks names/units: request_bytes/bytes (codex, claude; limit 400000),
  prompt_chars/chars (agy -p; limit 8000), packet_bytes/bytes (agy packet; limit 2000000).
  Equal to the limit is allowed; one more is input_too_large, CLI not called, exit 2.
Codex rollout: searched by thread id under <codex home>/sessions/ (all date folders);
  model_source.file is written relative to the codex home (~/.codex/... or $CODEX_HOME/...).
integrity_<N>.json: before, after, changed (bool). session_persistence for agy: "불가".
model_observed_set: sorted list (claude), null otherwise. usage-after_<N>.json
  tools.<tool>.exhausted = true on a limit failure.
Child environment: ANTHROPIC_API_KEY, OPENAI_API_KEY, CODEX_API_KEY, GEMINI_API_KEY and
  GOOGLE_API_KEY are removed (3: no API-key billing path); everything else passes through.
Saved files never contain accountId/installationId keys or values, the developer/user
  injected bodies of a Codex rollout, or agy log lines; stdout files are scrubbed too.
Failed rounds: no round_<N>.json, except that a contract failure may leave a failed
  round_<N>.json only if it records the failure (codex_invoked false or non-empty
  contract_violations). After contract failure the same tool may continue in the same
  run; after identity_unconfirmed the run stops; another tool needs a new run.
call_<N>.json keys used here: round, tool, kind, version, argv, cwd, started_at, ended_at,
  exit_code, failure, timeout_seconds, input_checks[{name, measured, limit, unit, ok}],
  model_requested, model_observed, model_observed_set, model_source{file, lines, field},
  model_check (일치|불일치|로그 없음|최종 작성자 미확인), effort_requested, effort_observed,
  formal, formal_reason, round_json, rank, downgraded_from, thread_id, conversation_id,
  request_sha256, injected_items[{line, role, head, sha256}], session_persistence, header.
Runner exit: 0 = call succeeded (review: formal round; context: answer accepted),
  2 = input_too_large (6.1), any other refusal or failure = non-zero.
"""
import ctypes
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / 'plugin/hglos/scripts/cross_host.py'
FIX = REPO / 'tests/fixtures/cross_host'
FAKE = FIX / 'fake_cli.py'
SCHEMA_PATH = REPO / 'plugin/hglos/skills/hglos-verify/assets/findings-schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
TOOLS = ('codex', 'claude', 'agy')
OPUS, SONNET, SOL = 'claude-opus-5-5', 'claude-sonnet-5-5', 'gpt-6.1-sol'
INSTRUCTION = 'SYNTHETIC-INSTRUCTION-MARKER Review only the scoped change.\n'
SYNTHETIC_IDS = ('example-acct-1', 'example-acct-2', 'example-install-1', 'example-credit-1', 'example-user-1')
UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
SYNTHETIC_UUID_RE = re.compile(r'00000000-0000-4000-8000-0000000000\d\d')
HOME_RE = re.compile(r'[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)([^\\/\s"\'`<>|*?,;:()\[\]{}]+)')
PERSONAL_RECORD_GLOBS = ('validation/*', '*.envelope.json', '*.stdout.txt', '*.stderr.txt', '*.stream.jsonl',
                         '*/call_*.json', 'call_*.json', '*/usage-*.json', 'usage-*.json',
                         '*agy-packet-*', '*.cli.txt', '*.log')
FORBIDDEN = {
    'codex': {'--dangerously-bypass-approvals-and-sandbox', '--dangerously-bypass-hook-trust',
              '--approve-for-me', '--add-dir', '--search', '--ephemeral', '--ignore-user-config',
              'resume', 'fork', '--last', 'workspace-write', 'danger-full-access', 'apply'},
    'claude': {'--dangerously-skip-permissions', '--allow-dangerously-skip-permissions'},
    'agy': {'--dangerously-skip-permissions', '--add-dir'},
}


def tid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def values(args, *names):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a in names]


def pid_alive(pid):
    if os.name == 'nt':
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


def kill(pid):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class CrossHostCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='hglos-cross-host-o-')
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.assertFalse(self.tmp.is_relative_to(REPO))
        self.repo = self.tmp / 'repo'
        self.temp = self.tmp / 'temp'
        self.codex_home = self.tmp / 'codexhome'
        self.home = self.tmp / 'home'
        self.cap = self.tmp / 'capture'
        self.scn = self.tmp / 'scenario.json'
        for p in (self.temp, self.codex_home, self.home, self.cap):
            p.mkdir()
        self.make_repo()
        self.declare()
        self.instruction = self.tmp / 'instruction.md'
        self.instruction.write_text(INSTRUCTION, encoding='utf-8')
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
        files = {'src/app.py': 'print("base")\n', 'other/out.py': 'print("out")\n',
                 'secret/key.txt': 'base\n', 'doc/CANON.md': '# canon\n'}
        for rel, text in files.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding='utf-8')
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        (self.repo / 'src/app.py').write_text('print("base")\nSCOPED_CHANGE_MARKER = 1\n', encoding='utf-8')
        (self.repo / 'other/out.py').write_text('print("out")\nOUT_OF_SCOPE_MARKER = 1\n', encoding='utf-8')
        (self.repo / 'secret/key.txt').write_text('base\nEXCLUDED_SECRET_MARKER\n', encoding='utf-8')
        (self.repo / 'src/new_file.py').write_text('UNTRACKED_SCOPED_CONTENT = 1\n', encoding='utf-8')

    def declare(self, vendors=('anthropic', 'openai', 'google'), quote='example declaration quote', raw=None,
                repo='repo'):
        path = self.repo / '.hglos/cross-host-egress.json'
        path.parent.mkdir(exist_ok=True)
        if raw is not None:
            path.write_text(raw, encoding='utf-8')
            return
        path.write_text(json.dumps({
            'schema_version': 1, 'vendors': list(vendors), 'repo': repo,
            'exclude_globs': ['secret/**'], 'account_data': {v: 'checked by example' for v in vendors},
            'declared_by': 'example', 'date': '2026-11-02', 'quote': quote}), encoding='utf-8')

    def scenario(self, **tools):
        self.scn.write_text(json.dumps(tools), encoding='utf-8')

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('HGLOS_', 'ANTHROPIC_', 'OPENAI_', 'GEMINI_', 'GOOGLE_', 'CODEX_'))}
        prefix = [sys.executable, '-B', str(FAKE)]
        for tool in TOOLS:
            env[f'HGLOS_CROSS_HOST_{tool.upper()}_CMD'] = json.dumps(prefix + [tool])
        env.update(TEMP=str(self.temp), TMP=str(self.temp), TMPDIR=str(self.temp),
                   CODEX_HOME=str(self.codex_home), HOME=str(self.home), USERPROFILE=str(self.home),
                   HGLOS_FAKE_CAPTURE=str(self.cap), HGLOS_FAKE_SCENARIO=str(self.scn),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env.update(extra)
        return env

    def run_cli(self, *args, env=None, timeout=120):
        return subprocess.run([sys.executable, '-B', str(CLI), *map(str, args)], cwd=self.tmp,
                              env=env or self.env(), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)

    def call(self, tool, kind='review', n=1, run='o-run', impl=OPUS, model=None, scope=('src', 'secret'),
             extra=(), env=None, instruction=None, timeout=120):
        args = ['call', '--repo', self.repo, '--run', run, '--round', n, '--tool', tool, '--kind', kind,
                '--instruction', instruction or self.instruction, '--canon', 'doc/CANON.md',
                '--implementer-model', impl]
        for s in scope:
            args += ['--scope', s]
        if model:
            args += ['--model', model]
        return self.run_cli(*args, *extra, env=env, timeout=timeout)

    def rundir(self, run='o-run'):
        return self.repo / 'validation' / run

    def tmprun(self, run='o-run'):
        return self.temp / 'hglos-cross-host' / run

    def callj(self, n=1, run='o-run'):
        path = self.rundir(run) / f'call_{n}.json'
        self.assertTrue(path.is_file(), f'missing {path}')
        return json.loads(path.read_text(encoding='utf-8'))

    def captures(self, tool, sub=None):
        out = [json.loads(p.read_text(encoding='utf-8'))
               for p in sorted(self.cap.glob(f'{tool}-*.json'), key=lambda p: int(p.stem.split('-')[-1]))]
        if sub == 'exec':
            out = [c for c in out if c['argv'][:1] == ['exec']]
        elif sub == 'app-server':
            out = [c for c in out if c['argv'][:1] == ['app-server']]
        elif sub == 'call' and tool == 'agy':
            out = [c for c in out if values(c['argv'], '-p') != ['/usage']]
        return out

    def last(self, tool, sub=None):
        items = self.captures(tool, sub)
        self.assertTrue(items, f'{tool} {sub or ""} was not invoked')
        return items[-1]

    def run_files_text(self, run='o-run'):
        return {p: p.read_bytes().decode('utf-8', 'replace')
                for p in self.rundir(run).rglob('*') if p.is_file()}

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def assert_failed(self, proc, failure, n=1, run='o-run'):
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        record = self.callj(n, run)
        self.assertEqual(record['failure'], failure)
        self.assertIs(record['formal'], False)
        self.assertIsNone(record.get('rank'))
        round_file = self.rundir(run) / f'round_{n}.json'
        if failure == 'contract' and round_file.exists():
            # A failed round file is tolerated only when it records the failure itself.
            data = json.loads(round_file.read_text(encoding='utf-8'))
            self.assertTrue(data.get('codex_invoked') is False or data.get('contract_violations'), data)
        else:
            self.assertFalse(round_file.exists())
        return record

    def assert_no_forbidden(self, tool, args, cwd=None):
        """Read-only and forbidden-flag contract shared by every call of that tool (6.1, 3)."""
        self.assertFalse(FORBIDDEN[tool] & set(args), args)
        if tool == 'codex':
            self.assertEqual(args[0], 'exec')
            self.assertEqual(values(args, '--sandbox', '-s'), ['read-only'])
            self.assertNotIn('--full-auto', args)
            self.assertEqual(len(values(args, '-m', '--model')), 1)
            config = values(args, '-c', '--config')
            self.assertEqual(len([c for c in config if c.startswith('model_reasoning_effort=')]), 1)
            self.assertIn(['--disable', 'memories'], [args[i:i + 2] for i in range(len(args))])
            self.assertIn('memories.use_memories=false', config)
            self.assertIn('memories.generate_memories=false', config)
        if tool == 'claude':
            self.assertEqual(values(args, '--permission-mode'), ['plan'])
            self.assertIn('--restricted', args)
            self.assertIn('--strict-mcp-config', args)
            self.assertIn('--no-session-persistence', args)
            self.assertEqual([set(v.split(',')) for v in values(args, '--tools')], [{'Read', 'Grep', 'Glob'}])
        if tool == 'agy':
            self.assertEqual(values(args, '--mode'), ['plan'])
            self.assertIn('--sandbox', args)
            self.assertEqual(len(values(args, '--model')), 1)
            if cwd is not None:
                self.assertFalse(Path(cwd).resolve().is_relative_to(self.repo.resolve()))
                self.assertTrue(Path(cwd).resolve().is_relative_to(self.tmprun_root().resolve()))

    def tmprun_root(self):
        return self.temp / 'hglos-cross-host'


# ======================================================== condition 1 / 6.1

class CallShape(CrossHostCase):
    def test_codex_review_argv_stdin_cwd_and_result_file(self):
        self.assert_ok(self.call('codex', impl=OPUS))
        cap = self.last('codex', 'exec')
        args = cap['argv']
        self.assertEqual(args[0], 'exec')
        self.assertEqual(values(args, '-m', '--model'), [SOL])
        self.assertIn('model_reasoning_effort="medium"', values(args, '-c', '--config'))
        self.assertIn(['--disable', 'memories'], [args[i:i + 2] for i in range(len(args))])
        self.assertIn('memories.use_memories=false', values(args, '-c', '--config'))
        self.assertIn('memories.generate_memories=false', values(args, '-c', '--config'))
        self.assertIn('--json', args)
        self.assertEqual(args[-1], '-')
        self.assertEqual(json.loads(cap['output_schema_text']), SCHEMA)
        out = values(args, '-o', '--output-last-message')
        self.assertEqual([Path(p).resolve() for p in out], [(self.rundir() / 'codex_1.raw.json').resolve()])
        self.assertEqual(Path(cap['cwd']).resolve(), self.repo.resolve())
        for cd in values(args, '--cd', '-C'):
            self.assertEqual(Path(cd).resolve(), self.repo.resolve())
        request = (self.rundir() / 'request_1.md').read_bytes()
        self.assertEqual(cap['stdin_sha256'], sha(request))
        self.assert_no_forbidden('codex', args)
        record = self.callj()
        self.assertEqual(record['tool'], 'codex')
        self.assertEqual(record['version'], 'codex-cli 0.164.0')
        self.assertEqual(record['argv'], args)
        self.assertEqual(record['request_sha256'], sha(request))
        self.assertEqual(record['timeout_seconds'], 900)
        self.assertEqual(record['round'], 1)
        self.assertTrue(record['started_at'] and record['ended_at'])
        self.assertEqual(record['exit_code'], 0)

    def test_codex_context_has_no_schema_and_answer_file(self):
        self.scenario(codex={'output': '# Context answer\nplain markdown\n'})
        self.assert_ok(self.call('codex', kind='context'))
        args = self.last('codex', 'exec')['argv']
        self.assertNotIn('--output-schema', args)
        out = values(args, '-o', '--output-last-message')
        self.assertEqual([Path(p).resolve() for p in out], [(self.rundir() / 'codex_1.answer.md').resolve()])
        self.assertEqual((self.rundir() / 'codex_1.answer.md').read_text(encoding='utf-8'),
                         '# Context answer\nplain markdown\n')
        self.assertEqual(self.callj()['timeout_seconds'], 600)
        self.assert_no_forbidden('codex', args)

    def test_claude_review_argv_and_inline_schema(self):
        self.assert_ok(self.call('claude', impl=SOL))
        cap = self.last('claude')
        args = cap['argv']
        self.assertTrue({'-p', '--print'} & set(args))
        self.assertEqual(values(args, '--model'), [OPUS])
        self.assertIn('--restricted', args)
        self.assertEqual([set(v.split(',')) for v in values(args, '--tools')], [{'Read', 'Grep', 'Glob'}])
        self.assertIn('--strict-mcp-config', args)
        self.assertEqual(values(args, '--output-format'), ['stream-json'])
        self.assertIn('--verbose', args)
        self.assertIn('--no-session-persistence', args)
        self.assertEqual([json.loads(v) for v in values(args, '--json-schema')], [SCHEMA])
        self.assertFalse(any('SYNTHETIC-INSTRUCTION-MARKER' in a for a in args))
        self.assertEqual(cap['stdin_sha256'], sha((self.rundir() / 'request_1.md').read_bytes()))
        self.assertEqual(Path(cap['cwd']).resolve(), self.repo.resolve())
        self.assert_no_forbidden('claude', args)
        self.assertTrue((self.rundir() / 'claude_1.stream.jsonl').is_file())
        self.assertEqual(json.loads((self.rundir() / 'claude_1.raw.json').read_text(encoding='utf-8')),
                         {'findings': []})

    def test_claude_context_has_no_schema_and_answer_from_result(self):
        self.scenario(claude={'result': 'Context answer text.\n'})
        self.assert_ok(self.call('claude', kind='context', impl=SOL))
        args = self.last('claude')['argv']
        self.assertNotIn('--json-schema', args)
        self.assertEqual((self.rundir() / 'claude_1.answer.md').read_text(encoding='utf-8'),
                         'Context answer text.\n')
        self.assert_no_forbidden('claude', args)

    def test_agy_review_runs_in_packet_outside_repo(self):
        model = 'claude-opus-5-5-high'
        self.assert_ok(self.call('agy', impl=SOL, model=model))
        cap = self.last('agy', 'call')
        args = cap['argv']
        packet = self.tmprun() / 'agy-packet-1'
        self.assertEqual(Path(cap['cwd']).resolve(), packet.resolve())
        self.assertFalse(packet.resolve().is_relative_to(self.repo.resolve()))
        self.assertEqual(values(args, '--model'), [model])
        self.assertIn('--sandbox', args)
        self.assertEqual(values(args, '--output-format'), ['json'])
        self.assertEqual([Path(p).resolve() for p in values(args, '--log-file')],
                         [(packet / 'agy_1.cli.txt').resolve()])
        # U13 D2: agy review uses the 900 s review timeout and --print-timeout 840s.
        self.assertEqual(self.callj()['timeout_seconds'], 900)
        self.assertEqual(values(args, '--print-timeout'), ['840s'])
        self.assertEqual(json.loads(cap['json_schema_text']), SCHEMA)
        prompt = values(args, '-p')
        self.assertEqual(len(prompt), 1)
        self.assertLessEqual(len(prompt[0]), 8000)
        self.assertEqual((self.rundir() / 'request_1.md').read_text(encoding='utf-8'), prompt[0])
        self.assertIn('SYNTHETIC-INSTRUCTION-MARKER', prompt[0])
        self.assert_no_forbidden('agy', args, cap['cwd'])
        self.assertTrue((self.rundir() / 'agy_1.envelope.json').is_file())
        self.assertTrue((packet / 'agy_1.cli.txt').is_file(), 'temporary folder must be kept after the run')

    def test_agy_context_print_timeout_and_no_schema(self):
        self.assert_ok(self.call('agy', kind='context', model='gemini-3.8-flash-low'))
        cap = self.last('agy', 'call')
        args = cap['argv']
        self.assert_no_forbidden('agy', args, cap['cwd'])
        self.assertEqual(values(args, '--print-timeout'), ['540s'])
        self.assertNotIn('--json-schema', args)
        self.assertEqual(self.callj()['timeout_seconds'], 600)
        self.assertEqual(self.callj()['session_persistence'], '불가')
        self.assertEqual((self.rundir() / 'agy_1.answer.md').read_text(encoding='utf-8'),
                         'Synthetic context answer.\n')

    def test_request_carries_scoped_diff_untracked_list_canon_and_excludes(self):
        self.assert_ok(self.call('codex'))
        text = (self.rundir() / 'request_1.md').read_text(encoding='utf-8')
        for needle in ('SYNTHETIC-INSTRUCTION-MARKER', '+SCOPED_CHANGE_MARKER = 1', 'src/new_file.py',
                       'doc/CANON.md', 'secret/**'):
            self.assertIn(needle, text)
        self.assertNotIn('OUT_OF_SCOPE_MARKER', text)
        self.assertNotIn('EXCLUDED_SECRET_MARKER', text)
        self.assertRegex(text, re.compile(r'다른 세션|other session', re.I))

    def test_agy_packet_is_allowlisted_copy_without_excluded_or_out_of_scope_files(self):
        self.assert_ok(self.call('agy', kind='context', model='gemini-3.8-flash-low'))
        packet = self.tmprun() / 'agy-packet-1'
        files = {p.relative_to(packet).as_posix(): p.read_bytes() for p in packet.rglob('*') if p.is_file()}
        joined = b'\n'.join(files.values())
        self.assertIn(b'+SCOPED_CHANGE_MARKER = 1', joined)
        copies = [k for k in files if k.endswith('src/new_file.py')]
        self.assertEqual(len(copies), 1, sorted(files))
        self.assertEqual(files[copies[0]], (self.repo / 'src/new_file.py').read_bytes())
        self.assertNotIn(b'EXCLUDED_SECRET_MARKER', joined)
        self.assertNotIn(b'OUT_OF_SCOPE_MARKER', joined)
        self.assertFalse([k for k in files if 'secret/' in k or k.endswith('key.txt')])

    def test_child_environment_drops_api_key_billing_variables(self):
        keys = {'ANTHROPIC_API_KEY': 'example-key-a', 'OPENAI_API_KEY': 'example-key-o',
                'CODEX_API_KEY': 'example-key-c', 'GEMINI_API_KEY': 'example-key-g',
                'GOOGLE_API_KEY': 'example-key-x'}
        env = self.env(**keys)
        for i, tool in enumerate(TOOLS, 1):
            model = 'gemini-3.8-flash-low' if tool == 'agy' else None
            self.assert_ok(self.call(tool, kind='context', run=f'env-{i}', model=model, env=env))
            cap = self.last(tool, 'exec' if tool == 'codex' else ('call' if tool == 'agy' else None))
            self.assertFalse(set(keys) & set(cap['env_keys']), tool)
            self.assertEqual(cap['env'].get('HGLOS_FAKE_CAPTURE'), str(self.cap), 'other env must pass through')

    def test_cli_is_called_without_shell_so_slash_usage_stays_literal(self):
        self.assert_ok(self.run_cli('usage', '--tools', 'agy'))
        cap = self.last('agy')
        self.assertEqual(cap['argv'][:4], ['-p', '/usage', '--output-format', 'json'])
        self.assertNotIn('--model', cap['argv'])

    def test_round_files_use_round_number_and_no_log_extension(self):
        self.assert_ok(self.call('claude', impl=SOL))
        names = {p.name for p in self.rundir().iterdir()}
        for name in ('request_1.md', 'call_1.json', 'usage-before_1.json', 'usage-after_1.json',
                     'claude_1.stream.jsonl', 'claude_1.raw.json', 'claude_1.stdout.txt',
                     'claude_1.stderr.txt', 'integrity_1.json', 'round_1.json'):
            self.assertIn(name, names)
        self.assertFalse([n for n in names if n.endswith('.log')])

    def test_existing_round_file_is_never_overwritten(self):
        self.rundir().mkdir(parents=True)
        keep = self.rundir() / 'request_1.md'
        keep.write_bytes(b'keep me')
        proc = self.call('codex')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(keep.read_bytes(), b'keep me')
        self.assertEqual(self.captures('codex', 'exec'), [])
        # Positive control: the refusal above is not merely a missing or broken runner.
        self.assert_ok(self.call('codex', run='control-run'))
        self.assertEqual(len(self.captures('codex', 'exec')), 1)

    def test_run_id_cannot_escape_validation_or_temp(self):
        # Positive control: a nested run id (validation/cross-host/<task>-<id>/, 6.1) works.
        self.assert_ok(self.call('codex', run='cross-host/O-fx1'))
        self.assertTrue((self.repo / 'validation/cross-host/O-fx1/call_1.json').is_file())
        for run in ('../escape', '..\\escape', str(self.tmp / 'abs-run'), 'a/../../escape'):
            with self.subTest(run=run):
                proc = self.call('codex', run=run)
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse((self.repo / 'escape').exists())
                self.assertFalse((self.tmp / 'abs-run').exists())
                self.assertFalse((self.temp / 'escape').exists())
        self.assertEqual(len(self.captures('codex', 'exec')), 1)

    def test_timeout_kills_the_process_tree(self):
        self.scenario(codex={'spawn_child_sleep': True, 'sleep': 60})
        started = time.monotonic()
        proc = self.call('codex', extra=['--timeout', '3'], timeout=90)
        elapsed = time.monotonic() - started
        cap = self.last('codex', 'exec')
        pids = [cap['pid'], *cap.get('child_pids', [])]
        self.addCleanup(lambda: [kill(p) for p in pids if pid_alive(p)])
        self.assertLess(elapsed, 45)
        record = self.assert_failed(proc, 'timeout')
        self.assertEqual(record['timeout_seconds'], 3)
        self.assertEqual(len(pids), 2)
        deadline = time.monotonic() + 10
        while any(map(pid_alive, pids)) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual([p for p in pids if pid_alive(p)], [], 'grandchild must die with the tree')

    def test_clean_removes_only_that_runs_temporary_folder(self):
        base = self.temp / 'hglos-cross-host'
        for rel in ('run-a/agy-packet-1/x.txt', 'run-b/agy-packet-1/y.txt'):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text('x', encoding='utf-8')
        (self.temp / 'other.txt').write_text('keep', encoding='utf-8')
        (self.rundir('run-a')).mkdir(parents=True)
        (self.rundir('run-a') / 'call_1.json').write_text('{}', encoding='utf-8')
        self.assertNotEqual(self.run_cli('clean', '--run', '../other').returncode, 0)
        self.assert_ok(self.run_cli('clean', '--run', 'run-a'))
        self.assertFalse((base / 'run-a').exists())
        self.assertTrue((base / 'run-b/agy-packet-1/y.txt').is_file())
        self.assertTrue((self.temp / 'other.txt').is_file())
        self.assertTrue((self.rundir('run-a') / 'call_1.json').is_file())

    def test_integrity_records_before_after_and_detects_repo_writes(self):
        self.assert_ok(self.call('codex', run='clean-run'))
        clean = json.loads((self.rundir('clean-run') / 'integrity_1.json').read_text(encoding='utf-8'))
        self.assertTrue({'before', 'after', 'changed'} <= clean.keys())
        self.assertIs(clean['changed'], False)
        self.scenario(codex={'write_repo_file': 'escaped.txt'})
        self.call('codex', run='dirty-run')
        dirty = json.loads((self.rundir('dirty-run') / 'integrity_1.json').read_text(encoding='utf-8'))
        self.assertIs(dirty['changed'], True)

    def test_account_identifiers_are_never_saved_and_temp_paths_are_relative(self):
        self.assert_ok(self.call('agy', kind='context', model='gemini-3.8-flash-low', run='agy-run'))
        self.assert_ok(self.call('codex', kind='context', run='codex-run'))
        for run in ('agy-run', 'codex-run'):
            for path, text in self.run_files_text(run).items():
                for value in SYNTHETIC_IDS + ('accountId', 'installationId', 'SYNTHETIC-DEV-BODY-SENTINEL',
                                              'SYNTHETIC-USER-BODY-SENTINEL', 'model_resolver.go:107]',
                                              'Initializing CLI store manager for workspace',
                                              'defaulting to CCPA'):
                    self.assertNotIn(value, text, f'{path.name} leaks {value}')
        for run, root in (('agy-run', self.tmprun('agy-run')), ('codex-run', self.codex_home)):
            text = (self.rundir(run) / 'call_1.json').read_text(encoding='utf-8')
            for form in {str(root.resolve()), root.resolve().as_posix(), json.dumps(str(root.resolve()))[1:-1]}:
                self.assertNotIn(form, text, f'{run}: absolute personal path recorded')
        self.assertTrue(self.callj(run='agy-run')['cwd'].replace('\\', '/').endswith('agy-packet-1'))


# ======================================================== condition 2 / 6.2

CODEX_DATA_KEYS = {'planType', 'primary', 'secondary', 'rateLimitReachedType',
                   'usedPercent', 'windowDurationMins', 'resetsAt'}
AGY_DATA_KEYS = {'groups', 'name', 'buckets', 'window', 'remaining_fraction', 'reset_time'}


def all_keys(node):
    if isinstance(node, dict):
        return set(node) | set().union(*(all_keys(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(set(), *(all_keys(v) for v in node))
    return set()


class Usage(CrossHostCase):
    def usage(self, *extra):
        proc = self.run_cli('usage', *extra)
        self.assert_ok(proc)
        return json.loads(proc.stdout)

    def test_codex_usage_keeps_allowed_fields_only_without_model_call(self):
        self.scenario(codex={'rate_limits': {'primary': {'usedPercent': 49, 'windowDurationMins': 10080,
                                                         'resetsAt': 1793774159},
                                             'secondary': {'usedPercent': 12, 'windowDurationMins': 300,
                                                           'resetsAt': 1793700000}}})
        out = self.usage('--tools', 'codex')
        codex = out['tools']['codex']
        self.assertEqual(codex['status'], 'ok')
        self.assertLessEqual(all_keys(codex['data']), CODEX_DATA_KEYS)
        self.assertEqual(codex['data']['primary']['usedPercent'], 49)
        self.assertEqual(codex['data']['secondary']['usedPercent'], 12)
        self.assertEqual(codex['data']['planType'], 'prolite')
        text = json.dumps(out)
        for value in SYNTHETIC_IDS + ('C:\\\\Users\\\\example', 'codexHome', 'accountId', 'installationId'):
            self.assertNotIn(value, text)
        self.assertEqual([c['argv'][0] for c in self.captures('codex')], ['app-server'])

    def test_agy_usage_success_requires_usage_command_zero_turns_and_zero_tokens(self):
        cases = {'ok': {}, 'turns': {'num_turns': 1, 'total_tokens': 10703},
                 'tokens': {'total_tokens': 5}, 'name': {'command_name': 'chat'},
                 'missing': {'drop_command': True}}
        for label, usage in cases.items():
            with self.subTest(label=label):
                self.scenario(agy={'usage': usage})
                agy = self.usage('--tools', 'agy')['tools']['agy']
                if label == 'ok':
                    self.assertEqual(agy['status'], 'ok')
                    self.assertLessEqual(all_keys(agy['data']), AGY_DATA_KEYS)
                    names = [g['name'] for g in agy['data']['groups']]
                    self.assertEqual(names, ['Gemini Models', 'Claude and GPT models', 'Gemini 4 Argon'])
                    self.assertEqual(agy['data']['groups'][0]['buckets'][0]['remaining_fraction'], 0.8)
                else:
                    self.assertEqual(agy['status'], 'unknown')
                    self.assertIsNone(agy['data'])
                if label == 'turns':
                    self.assertEqual(agy['tokens_consumed'], 10703)
                self.assertNotIn('example-acct-2', json.dumps(agy))

    def test_claude_usage_is_unknown_with_alternatives_and_no_cli_call(self):
        claude = self.usage('--tools', 'claude', '--claude-usage-note', 'example note')['tools']['claude']
        self.assertEqual(claude['status'], 'unknown')
        self.assertIsNone(claude['data'])
        self.assertTrue(claude['alternatives'])
        self.assertEqual(claude['note'], 'example note')
        self.assertEqual(self.captures('claude'), [])

    def test_codex_app_server_failure_is_unknown_not_full(self):
        self.scenario(codex={'app_server_fail': True})
        codex = self.usage('--tools', 'codex')['tools']['codex']
        self.assertEqual(codex['status'], 'unknown')
        self.assertIsNone(codex['data'])

    def test_call_writes_usage_before_and_after_for_the_called_tool(self):
        self.assert_ok(self.call('codex'))
        for phase in ('before', 'after'):
            data = json.loads((self.rundir() / f'usage-{phase}_1.json').read_text(encoding='utf-8'))
            self.assertEqual(set(data['tools']), {'codex'})
            self.assertLessEqual(all_keys(data['tools']['codex']['data']), CODEX_DATA_KEYS)
            self.assertNotIn('example-acct-1', json.dumps(data))

    def test_call_refuses_below_threshold_before_invoking(self):
        cases = [('codex', None, {'codex': {'rate_limits': {'primary': {'usedPercent': 91, 'windowDurationMins': 10080,
                                                                         'resetsAt': 1}}}}),
                 ('agy', 'gemini-3.8-flash-low',
                  {'agy': {'usage': {'groups': {'Gemini Models': [['weekly', 0.8], ['5h', 0.0999]]}}}}),
                 ('agy', 'claude-opus-5-5-high',
                  {'agy': {'usage': {'groups': {'Claude and GPT models': [['weekly', 0.016], ['5h', 1.0]]}}}})]
        for i, (tool, model, sc) in enumerate(cases):
            with self.subTest(tool=tool, model=model):
                self.scenario(**sc)
                run = f'limit-{i}'
                self.assert_failed(self.call(tool, run=run, impl=SOL, model=model), 'limit', run=run)
        self.assertEqual(self.captures('codex', 'exec'), [])
        self.assertEqual(self.captures('agy', 'call'), [])

    def test_threshold_boundaries_are_not_below(self):
        self.scenario(codex={'rate_limits': {'primary': {'usedPercent': 90, 'windowDurationMins': 10080, 'resetsAt': 1}}},
                      agy={'usage': {'groups': {'Gemini Models': [['weekly', 0.1], ['5h', 0.1]]}}})
        self.assert_ok(self.call('codex', run='edge-codex'))
        self.assert_ok(self.call('agy', kind='context', run='edge-agy', model='gemini-3.8-flash-low'))


# ======================================================== condition 3 / 6.2 routes

REVIEW_ORDER = {
    (SOL, 'codex'): ['claude-p', 'agy-claude', 'native-codex', 'handoff'],
    (OPUS, 'claude'): ['codex-exec', 'native-claude', 'handoff'],
    # 6.2 lists "새 codex exec 또는 native 새 맥락 Codex 검증자" in one cell. U13 D1 (JH):
    # native first, following HOST.md "불필요한 Codex→Codex 중첩보다 네이티브 우선".
    (OPUS, 'codex'): ['native-codex', 'codex-exec', 'handoff'],
    (SOL, 'claude'): ['native-claude', 'claude-p', 'handoff'],
}
CONTEXT_ORDER = {
    'codex': ['agy-gemini', 'claude-p', 'native-codex'],
    'claude': ['agy-gemini', 'codex-exec', 'native-claude'],
}
HEALTHY = {'codex': {'rate_limits': {'primary': {'usedPercent': 50, 'windowDurationMins': 10080, 'resetsAt': 1}}},
           'agy': {'usage': {'groups': {'Gemini Models': [['weekly', 0.8], ['5h', 0.9]],
                                        'Claude and GPT models': [['weekly', 0.8], ['5h', 1.0]],
                                        'Gemini 4 Argon': [['weekly', 0.5]]}}}}
S0_VALUES = {'codex': {'rate_limits': {'primary': {'usedPercent': 50, 'windowDurationMins': 10080, 'resetsAt': 1}}},
             'agy': {'usage': {'groups': {'Gemini Models': [['weekly', 0.783], ['5h', 0.997]],
                                          'Claude and GPT models': [['weekly', 0.016], ['5h', 1.0]],
                                          'Gemini 4 Argon': [['weekly', 0.0]]}}}}
CODEX_HOT = {'codex': {'rate_limits': {'primary': {'usedPercent': 50, 'windowDurationMins': 10080, 'resetsAt': 1},
                                       'secondary': {'usedPercent': 91, 'windowDurationMins': 300, 'resetsAt': 1}}},
             'agy': HEALTHY['agy']}
GEMINI_LOW = {'codex': HEALTHY['codex'],
              'agy': {'usage': {'groups': {'Gemini Models': [['weekly', 0.8], ['5h', 0.05]],
                                           'Claude and GPT models': [['weekly', 0.8], ['5h', 1.0]],
                                           'Gemini 4 Argon': [['weekly', 0.0]]}}}}


class Routing(CrossHostCase):
    def route(self, usage_scenario, impl, main, kind):
        self.scenario(**usage_scenario)
        proc = self.run_cli('usage')
        self.assert_ok(proc)
        usage = self.tmp / 'usage.json'
        usage.write_text(proc.stdout, encoding='utf-8')
        args = ['route', '--usage', usage, '--implementer-model', impl, '--main', main, '--kind', kind]
        first, second = self.run_cli(*args), self.run_cli(*args)
        self.assert_ok(first)
        self.assertEqual(first.stdout, second.stdout, 'routing must be deterministic')
        return json.loads(first.stdout)

    def test_review_table_full_order_and_selection_when_healthy(self):
        for (impl, main), order in REVIEW_ORDER.items():
            with self.subTest(impl=impl, main=main):
                out = self.route(HEALTHY, impl, main, 'review')
                self.assertEqual(out['order'], order)
                self.assertEqual(out['selected'], order[0])
                self.assertEqual(out['skipped'], [])

    def test_context_table_and_argon_only_with_remaining(self):
        for main, order in CONTEXT_ORDER.items():
            with self.subTest(main=main, usage='healthy'):
                out = self.route(HEALTHY, SOL, main, 'context')
                self.assertEqual(out['order'], order)
                self.assertEqual(out['selected'], 'agy-gemini')
                self.assertTrue(out['selected_model'].startswith('gemini-4-argon'))
            with self.subTest(main=main, usage='s0'):
                out = self.route(S0_VALUES, SOL, main, 'context')
                self.assertEqual(out['selected'], 'agy-gemini')
                self.assertTrue(out['selected_model'].startswith('gemini-3.8-flash'))
            with self.subTest(main=main, usage='gemini-low'):
                out = self.route(GEMINI_LOW, SOL, main, 'context')
                self.assertEqual(out['selected'], order[1])
                self.assertIn({'route': 'agy-gemini', 'reason': 'below_threshold'}, out['skipped'])

    def test_s0_values_skip_agy_claude_and_keep_claude_unknown(self):
        out = self.route(S0_VALUES, SOL, 'codex', 'review')
        self.assertEqual(out['selected'], 'claude-p')
        self.assertIs(out['limit_unknown'], True)
        self.assertEqual(out['selected_model'], OPUS)
        self.assertIn({'route': 'agy-claude', 'reason': 'below_threshold'}, out['skipped'])

    def test_codex_secondary_window_over_ninety_percent_is_skipped(self):
        out = self.route(CODEX_HOT, OPUS, 'claude', 'review')
        self.assertEqual(out['selected'], 'native-claude')
        self.assertIn({'route': 'codex-exec', 'reason': 'below_threshold'}, out['skipped'])

    def test_first_choice_is_never_the_implementer_family(self):
        for (impl, main), order in REVIEW_ORDER.items():
            family = 'claude' if impl.startswith('claude') else 'codex'
            with self.subTest(impl=impl, main=main):
                first = self.route(HEALTHY, impl, main, 'review')['selected']
                if family == 'claude':
                    self.assertNotIn(first, {'claude-p', 'agy-claude', 'native-claude'})
                else:
                    self.assertNotIn(first, {'codex-exec', 'native-codex'})


# ======================================================== condition 4 / 6.3

class Identity(CrossHostCase):
    def test_1_codex_match_is_formal_with_source_and_rank(self):
        self.assert_ok(self.call('codex', impl=OPUS))
        r = self.callj()
        self.assertEqual((r['model_requested'], r['model_observed'], r['model_check']), (SOL, SOL, '일치'))
        self.assertEqual((r['effort_requested'], r['effort_observed']), ('medium', 'medium'))
        self.assertEqual(r['thread_id'], tid(1))
        src = r['model_source']
        self.assertTrue(src['file'].replace('\\', '/').endswith(
            f'sessions/2026/11/02/rollout-2026-11-02T13-00-00-{tid(1)}.jsonl'))
        self.assertEqual(src['lines'], [10])
        self.assertEqual(src['field'], 'payload.model')
        self.assertIs(r['formal'], True)
        self.assertEqual(r['round_json'], 'round_1.json')
        self.assertTrue((self.rundir() / 'round_1.json').is_file())
        self.assertEqual(r['rank'], 1)
        self.assertIsNone(r['failure'])
        for piece in (f'요청 모델 {SOL}', f'관측 모델 {SOL}', '판정: 일치', '순위: 1', f'{tid(1)}.jsonl'):
            self.assertIn(piece, r['header'])

    def test_1b_effort_difference_does_not_affect_identity(self):
        self.scenario(codex={'rollout_effort': 'low'})
        self.assert_ok(self.call('codex', impl=OPUS))
        r = self.callj()
        self.assertEqual((r['effort_requested'], r['effort_observed']), ('medium', 'low'))
        self.assertEqual((r['model_check'], r['rank']), ('일치', 1))
        self.assertIs(r['formal'], True)

    def test_2_mismatch_is_not_formal(self):
        self.scenario(codex={'rollout_model': 'gpt-6.1-other'})
        r = self.assert_failed(self.call('codex'), 'identity_unconfirmed')
        self.assertEqual((r['model_observed'], r['model_check']), ('gpt-6.1-other', '불일치'))
        self.assertIsNone(r['rank'])
        self.assertIn('순위: 미확인(', r['header'])

    def test_2b_any_turn_context_disagreeing_is_mismatch(self):
        self.scenario(codex={'extra_turn_context_models': ['gpt-6.1-other']})
        r = self.assert_failed(self.call('codex'), 'identity_unconfirmed')
        self.assertEqual(r['model_check'], '불일치')

    def test_3_no_log_is_not_formal(self):
        self.scenario(codex={'no_rollout': True})
        r = self.assert_failed(self.call('codex'), 'identity_unconfirmed')
        self.assertEqual(r['model_check'], '로그 없음')
        self.assertIsNone(r['model_observed'])

    def test_4_log_of_another_call_is_not_used(self):
        cases = [('codex', None, {'codex': {'rollout_thread': tid(9)}}),
                 ('agy', 'claude-opus-5-5-high', {'agy': {'log_workspace': 'C:\\Users\\example\\elsewhere'}}),
                 ('agy', 'claude-opus-5-5-high', {'agy': {'no_log': True}}),
                 ('agy', 'claude-opus-5-5-high', {'agy': {'resolving': []}})]
        for i, (tool, model, sc) in enumerate(cases):
            with self.subTest(case=i):
                self.scenario(**sc)
                run = f'other-log-{i}'
                r = self.assert_failed(self.call(tool, run=run, impl=SOL, model=model),
                                       'identity_unconfirmed', run=run)
                self.assertEqual(r['model_check'], '로그 없음')

    def test_4b_agy_resolving_lines_must_all_match_request(self):
        self.scenario(agy={'resolving': ['claude-opus-5-5-high', 'claude-sonnet-5-5-high']})
        r = self.assert_failed(self.call('agy', impl=SOL, model='claude-opus-5-5-high'), 'identity_unconfirmed')
        self.assertEqual(r['model_check'], '불일치')

    def test_4c_agy_match_records_log_source_and_conversation(self):
        self.assert_ok(self.call('agy', impl=SOL, model='claude-opus-5-5-high'))
        r = self.callj()
        self.assertEqual((r['model_observed'], r['model_check'], r['rank']), ('claude-opus-5-5-high', '일치', 1))
        self.assertEqual(r['conversation_id'], tid(21))
        self.assertTrue(r['model_source']['file'].replace('\\', '/').endswith('agy-packet-1/agy_1.cli.txt'))
        self.assertEqual(r['model_source']['lines'], [5, 6])

    def test_5_claude_final_author_missing(self):
        self.scenario(claude={'omit_final_model': True})
        r = self.assert_failed(self.call('claude', impl=SOL), 'identity_unconfirmed')
        self.assertEqual(r['model_check'], '최종 작성자 미확인')
        self.assertIsNone(r['model_observed'])
        self.assertEqual(r['model_observed_set'], [OPUS])
        self.assertIn('순위: 미확인(', r['header'])

    def test_5b_claude_final_model_decides_and_other_models_are_recorded(self):
        self.scenario(claude={'model_usage': [OPUS, 'claude-haiku-5-5'], 'assistant_models': ['claude-haiku-5-5', OPUS]})
        self.assert_ok(self.call('claude', impl=SOL, run='set-run'))
        r = self.callj(run='set-run')
        self.assertEqual((r['model_observed'], r['model_check'], r['rank']), (OPUS, '일치', 1))
        self.assertEqual(r['model_observed_set'], sorted([OPUS, 'claude-haiku-5-5']))
        self.scenario(claude={'final_model': SONNET})
        r = self.assert_failed(self.call('claude', impl=SOL, run='final-run'), 'identity_unconfirmed', run='final-run')
        self.assertEqual(r['model_check'], '불일치')

    def test_6_excluded_model_matches_but_is_not_formal(self):
        r = self.assert_failed(self.call('claude', impl=OPUS, model=SONNET), 'identity_unconfirmed')
        self.assertEqual(r['model_check'], '일치')
        self.assertIsNone(r['rank'])
        self.assertTrue(r['formal_reason'])

    def test_7_model_change_inside_run_is_not_formal(self):
        self.assert_ok(self.call('claude', impl=SOL, n=1))
        proc = self.call('claude', impl=SOL, n=2, model=SONNET)
        self.assert_failed(proc, 'identity_unconfirmed', n=2)
        self.assertTrue((self.rundir() / 'round_1.json').is_file())

    def test_8_reused_thread_id_fails_fresh_session(self):
        self.assert_ok(self.call('codex', n=1))
        r = self.assert_failed(self.call('codex', n=2), 'identity_unconfirmed', n=2)
        self.assertEqual(r['thread_id'], tid(1))
        self.assertEqual(r['model_check'], '일치', 'F1 failure is separate from the model check')

    def test_rank_table(self):
        cases = [('claude', OPUS, SOL, 1), ('claude', OPUS, OPUS, 3), ('claude', SONNET, SOL, 1),
                 ('claude', SONNET, OPUS, None), ('codex', SOL, OPUS, 1), ('codex', SOL, SOL, 3),
                 ('agy', 'claude-opus-5-5-high', SOL, 1), ('agy', 'claude-opus-5-5-high', OPUS, 3),
                 ('agy', 'claude-sonnet-5-5-high', OPUS, None), ('agy', 'claude-sonnet-5-5-high', SOL, 1)]
        for i, (tool, model, impl, rank) in enumerate(cases):
            with self.subTest(tool=tool, model=model, impl=impl):
                run = f'rank-{i}'
                proc = self.call(tool, run=run, impl=impl, model=model)
                r = self.callj(run=run)
                self.assertEqual(r['model_check'], '일치')
                self.assertEqual(r['rank'], rank)
                self.assertIs(r['formal'], rank is not None)
                self.assertEqual((self.rundir(run) / 'round_1.json').is_file(), rank is not None)
                self.assertEqual(proc.returncode == 0, rank is not None, proc.stderr)

    def test_never_used_models_are_refused_before_calling(self):
        cases = [('review', 'gemini-3.8-flash-low', SOL), ('review', 'gemini-3.8-flash-low', OPUS),
                 ('review', 'gpt-oss-120b-medium', OPUS), ('context', 'gpt-oss-120b-medium', SOL)]
        for i, (kind, model, impl) in enumerate(cases):
            with self.subTest(kind=kind, model=model, impl=impl):
                run = f'never-{i}'
                proc = self.call('agy', kind=kind, run=run, impl=impl, model=model)
                self.assertNotEqual(proc.returncode, 0)
                r = self.callj(run=run)
                self.assertTrue(r['failure'])
                self.assertIs(r['formal'], False)
                self.assertIsNone(r['rank'])
                self.assertFalse((self.rundir(run) / 'round_1.json').exists())
        self.assertEqual(self.captures('agy', 'call'), [])
        # Positive control: the same tool and kind with an allowed model is called.
        self.assert_ok(self.call('agy', kind='context', run='never-ok', model='gemini-3.8-flash-low'))
        self.assertEqual(len(self.captures('agy', 'call')), 1)

    def test_context_records_identity_without_rank_or_round(self):
        self.assert_ok(self.call('claude', kind='context', impl=SOL))
        r = self.callj()
        self.assertEqual(r['model_check'], '일치')
        self.assertIsNone(r['rank'])
        self.assertIs(r['formal'], False)
        self.assertIsNone(r['round_json'])
        self.assertFalse((self.rundir() / 'round_1.json').exists())
        self.assertIsNone(r['failure'])


class FreshContextF3(CrossHostCase):
    def test_r2_structure_passes_and_only_heads_and_hashes_are_kept(self):
        self.assert_ok(self.call('codex'))
        r = self.callj()
        template = json.loads((FIX / 'codex_rollout_r2.json').read_text(encoding='utf-8'))['lines']
        texts = [line['payload']['content'][0]['text'] for line in template[:11]
                 if line.get('payload', {}).get('type') == 'message']
        items = r['injected_items']
        self.assertEqual([i['role'] for i in items], ['developer'] * 5 + ['user'])
        self.assertEqual([i['line'] for i in items], [3, 4, 5, 6, 7, 8])
        for item, text in zip(items, texts):
            self.assertEqual(item['sha256'], sha(text.encode('utf-8')))
            first = text.split('\n')[0]
            self.assertTrue(first.startswith(item['head']) and item['head'], item)
            self.assertLessEqual(len(item['head']), 80)
        for text in self.run_files_text().values():
            self.assertNotIn('SENTINEL', text)

    def test_memory_off_and_environment_context_still_pass(self):
        self.scenario(codex={'memory_off': True, 'environment_context_before_request': True})
        self.assert_ok(self.call('codex'))
        heads = [i['head'] for i in self.callj()['injected_items']]
        self.assertTrue(any(h.startswith('<environment_context>') for h in heads))
        self.assertFalse(any(h.startswith('## Memory') for h in heads))

    def test_unexpected_user_message_before_request_fails(self):
        self.scenario(codex={'extra_user_before_request': 'PRIOR-ROUND-MARKER previous findings'})
        self.assert_failed(self.call('codex'), 'identity_unconfirmed')

    def test_request_hash_mismatch_fails(self):
        self.scenario(codex={'rollout_request': 'a different request body'})
        self.assert_failed(self.call('codex'), 'identity_unconfirmed')

    def test_argv_never_resumes_or_forks(self):
        self.assert_ok(self.call('codex', n=1))
        self.scenario(codex={'thread_id': tid(3)})
        self.assert_ok(self.call('codex', n=2))
        for cap in self.captures('codex', 'exec'):
            self.assertFalse({'resume', 'fork', '--last'} & set(cap['argv']))
        self.assertTrue((self.rundir() / 'round_2.json').is_file())


# ======================================================== condition 5 / 6.4

class Failures(CrossHostCase):
    def calls(self, tool):
        return self.captures(tool, {'codex': 'exec', 'agy': 'call'}.get(tool))

    def test_auth_failures_keep_raw_text_and_call_once(self):
        self.scenario(claude={'auth_fail': True})
        self.assert_failed(self.call('claude', impl=SOL, run='c-auth'), 'auth', run='c-auth')
        self.assertIn('OAuth session expired',
                      (self.rundir('c-auth') / 'claude_1.stream.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(len(self.calls('claude')), 1, 'no silent retry')
        for i, text in enumerate(('You are not logged into Antigravity\n', 'authentication failed or timed out\n')):
            self.scenario(agy={'exit': 1, 'stderr': text})
            run = f'a-auth-{i}'
            self.assert_failed(self.call('agy', impl=SOL, run=run, model='claude-opus-5-5-high'), 'auth', run=run)
            self.assertIn(text.strip(), (self.rundir(run) / 'agy_1.stderr.txt').read_text(encoding='utf-8'))
            self.assertEqual(len(self.calls('agy')), i + 1, 'no silent retry')

    def test_sandbox_network_failures(self):
        for i, text in enumerate(('dial tcp: lookup example.invalid: no such host\n',
                                  'open C:\\Users\\example\\.gemini\\log: Access is denied.\n')):
            self.scenario(agy={'exit': 1, 'stderr': text})
            run = f'net-{i}'
            self.assert_failed(self.call('agy', impl=SOL, run=run, model='claude-opus-5-5-high'),
                               'sandbox_network', run=run)
            self.assertEqual(len(self.calls('agy')), i + 1, 'no silent retry')

    def test_claude_budget_stop_is_limit_and_marks_usage_after(self):
        self.scenario(claude={'subtype': 'error_max_budget_usd'})
        self.assert_failed(self.call('claude', impl=SOL), 'limit')
        after = json.loads((self.rundir() / 'usage-after_1.json').read_text(encoding='utf-8'))
        self.assertIs(after['tools']['claude']['exhausted'], True)
        self.assertEqual(len(self.calls('claude')), 1, 'no silent retry')

    def test_contract_failures_never_repair_raw_output(self):
        dup = {'path': 'src/app.py', 'line': 2, 'category': 'logic', 'severity': 'low',
               'evidence': 'observed', 'claim': 'synthetic duplicate', 'detail': 'synthetic.'}
        dup_text = json.dumps({'findings': [dup, dup]})
        cases = [('codex', 'review', {'codex': {'output': 'not json'}}, 'codex_1.raw.json', b'not json'),
                 ('codex', 'review', {'codex': {'output': '{"findings": [{"path": "a"}]}'}},
                  'codex_1.raw.json', b'{"findings": [{"path": "a"}]}'),
                 # validate_findings accepts this, make_round_json reports a duplicate fingerprint
                 # in contract_violations (6.1: do not trust the converter exit code alone).
                 ('codex', 'review', {'codex': {'output': dup_text}}, 'codex_1.raw.json', dup_text.encode()),
                 ('codex', 'review', {'codex': {'no_output': True}}, None, None),
                 ('codex', 'context', {'codex': {'output': ''}}, None, None),
                 ('claude', 'review', {'claude': {'omit_structured': True}}, None, None),
                 ('claude', 'context', {'claude': {'result': ''}}, None, None),
                 ('agy', 'review', {'agy': {'response': 'not json'}}, None, None),
                 ('agy', 'context', {'agy': {'response': ''}}, None, None)]
        for i, (tool, kind, sc, name, raw) in enumerate(cases):
            with self.subTest(tool=tool, kind=kind, case=i):
                before = len(self.calls(tool))
                self.scenario(**sc)
                run = f'contract-{i}'
                model = 'claude-opus-5-5-high' if tool == 'agy' else None
                self.assert_failed(self.call(tool, kind=kind, run=run, impl=SOL, model=model), 'contract', run=run)
                self.assertEqual(len(self.calls(tool)), before + 1, 'no silent retry')
                if name:
                    self.assertEqual((self.rundir(run) / name).read_bytes(), raw)

    def test_same_tool_continues_after_contract_failure(self):
        self.scenario(codex={'output': 'not json'})
        self.assert_failed(self.call('codex', n=1), 'contract')
        self.scenario(codex={'thread_id': tid(2)})
        self.assert_ok(self.call('codex', n=2))
        self.assertIs(self.callj(2)['formal'], True)
        self.assertTrue((self.rundir() / 'round_2.json').is_file())

    def test_failed_identity_stops_the_run(self):
        self.scenario(codex={'rollout_model': 'gpt-6.1-other'})
        self.assert_failed(self.call('codex', n=1), 'identity_unconfirmed')
        self.scenario(codex={'thread_id': tid(2)})
        proc = self.call('codex', n=2)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(len(self.calls('codex')), 1)
        self.assertFalse((self.rundir() / 'round_2.json').exists())

    def test_downgrade_needs_a_new_run(self):
        self.scenario(claude={'auth_fail': True})
        self.assert_failed(self.call('claude', impl=SOL, run='first'), 'auth', run='first')
        proc = self.call('codex', n=2, impl=SOL, run='first')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.calls('codex'), [])
        self.assert_ok(self.call('agy', impl=SOL, run='second', model='claude-opus-5-5-high',
                                 extra=['--downgraded-from', 'first']))
        self.assertEqual(self.callj(run='second')['downgraded_from'], 'first')


# ======================================================== condition 7 / 6.6 and 6.1 input limits

class EgressAndLimits(CrossHostCase):
    def assert_refused(self, proc, failure, tool, run='o-run'):
        self.assert_failed(proc, failure, run=run)
        sub = {'codex': 'exec', 'agy': 'call'}.get(tool)
        self.assertEqual(self.captures(tool, sub), [])

    def test_missing_or_invalid_declaration_refuses(self):
        (self.repo / '.hglos/cross-host-egress.json').unlink()
        self.assert_refused(self.call('codex', run='none'), 'egress_refused', 'codex', run='none')
        self.declare(raw='{not json')
        self.assert_refused(self.call('codex', run='bad'), 'egress_refused', 'codex', run='bad')
        self.declare(quote='')
        self.assert_refused(self.call('codex', run='noquote'), 'egress_refused', 'codex', run='noquote')
        self.declare(repo='other-repo')
        self.assert_refused(self.call('codex', run='otherrepo'), 'egress_refused', 'codex', run='otherrepo')
        # Positive control: a complete declaration for this repository is accepted.
        self.declare()
        self.assert_ok(self.call('codex', run='declared'))

    def test_vendor_must_be_declared_per_tool(self):
        self.declare(vendors=('anthropic',))
        self.assert_refused(self.call('codex', run='openai'), 'egress_refused', 'codex', run='openai')
        self.assert_refused(self.call('agy', run='google', model='gemini-3.8-flash-low', kind='context'),
                            'egress_refused', 'agy', run='google')
        # U13 D4: agy with a Claude model needs google and anthropic.
        self.assert_refused(self.call('agy', run='agy-cl-1', impl=SOL, model='claude-opus-5-5-high'),
                            'egress_refused', 'agy', run='agy-cl-1')
        self.assert_ok(self.call('claude', impl=SOL, run='anthropic'))
        self.declare(vendors=('google',))
        self.assert_refused(self.call('agy', run='agy-cl-2', impl=SOL, model='claude-opus-5-5-high'),
                            'egress_refused', 'agy', run='agy-cl-2')
        # Positive controls: Gemini needs google only; Claude via agy passes with both.
        self.assert_ok(self.call('agy', run='google-ok', model='gemini-3.8-flash-low', kind='context'))
        self.declare(vendors=('google', 'anthropic'))
        self.assert_ok(self.call('agy', run='agy-cl-ok', impl=SOL, model='claude-opus-5-5-high'))
        self.assertEqual(len(self.captures('agy', 'call')), 2)

    def write(self, name, text):
        path = self.tmp / name
        path.write_bytes(text.encode('utf-8'))
        return path

    def check(self, run, name):
        return {c['name']: c for c in self.callj(run=run)['input_checks']}[name]

    @staticmethod
    def utf8_text(nbytes):
        """Text of exactly nbytes UTF-8 bytes, mostly 3-byte Korean, so chars != bytes."""
        k = max(nbytes // 3 - 10, 0)
        return '가' * k + 'x' * (nbytes - 3 * k)

    # Run ids and instruction file names keep equal lengths inside each test so that a
    # runner which writes them into the request keeps a constant overhead.
    def test_request_byte_limit_codex_and_claude(self):
        for tool in ('codex', 'claude'):
            with self.subTest(tool=tool):
                kw = dict(kind='context', impl=SOL)
                self.assert_ok(self.call(tool, run=f'{tool}-1', instruction=self.write('i1.md', 'x' * 100), **kw))
                cal = self.check(f'{tool}-1', 'request_bytes')
                self.assertEqual((cal['limit'], cal['unit']), (400000, 'bytes'))
                room = 400000 - (cal['measured'] - 100)
                self.assert_ok(self.call(tool, run=f'{tool}-2', instruction=self.write('i2.md', 'x' * room), **kw))
                self.assertEqual((self.check(f'{tool}-2', 'request_bytes')['measured'],
                                  self.check(f'{tool}-2', 'request_bytes')['ok']), (400000, True))
                proc = self.call(tool, run=f'{tool}-3', instruction=self.write('i3.md', 'x' * (room + 1)), **kw)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(self.callj(run=f'{tool}-3')['failure'], 'input_too_large')
                self.assertEqual((self.check(f'{tool}-3', 'request_bytes')['measured'],
                                  self.check(f'{tool}-3', 'request_bytes')['ok']), (400001, False))
                # Non-ASCII: the limit counts UTF-8 bytes, not characters.
                self.assert_ok(self.call(tool, run=f'{tool}-4', instruction=self.write('i4.md', self.utf8_text(room)), **kw))
                self.assertEqual(self.check(f'{tool}-4', 'request_bytes')['measured'], 400000)
                proc = self.call(tool, run=f'{tool}-5', instruction=self.write('i5.md', self.utf8_text(room + 1)), **kw)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(self.check(f'{tool}-5', 'request_bytes')['measured'], 400001)
        self.assertEqual(len(self.captures('codex', 'exec')), 3)
        self.assertEqual(len(self.captures('claude')), 3)

    def test_agy_prompt_char_limit(self):
        kw = dict(kind='context', model='gemini-3.8-flash-low')
        self.assert_ok(self.call('agy', run='p-1', instruction=self.write('i1.md', 'x' * 100), **kw))
        cal = self.check('p-1', 'prompt_chars')
        self.assertEqual((cal['limit'], cal['unit']), (8000, 'chars'))
        room = 8000 - (cal['measured'] - 100)
        self.assert_ok(self.call('agy', run='p-2', instruction=self.write('i2.md', 'x' * room), **kw))
        self.assertEqual(len(values(self.last('agy', 'call')['argv'], '-p')[0]), 8000)
        proc = self.call('agy', run='p-3', instruction=self.write('i3.md', 'x' * (room + 1)), **kw)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.callj(run='p-3')['failure'], 'input_too_large')
        self.assertEqual(self.check('p-3', 'prompt_chars')['measured'], 8001)
        # Non-ASCII: the -p limit counts characters, not bytes.
        self.assert_ok(self.call('agy', run='p-4', instruction=self.write('i4.md', '가' * room), **kw))
        self.assertEqual(self.check('p-4', 'prompt_chars')['measured'], 8000)
        proc = self.call('agy', run='p-5', instruction=self.write('i5.md', '가' * (room + 1)), **kw)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.check('p-5', 'prompt_chars')['measured'], 8001)
        self.assertEqual(len(self.captures('agy', 'call')), 3)

    def test_agy_packet_byte_limit(self):
        kw = dict(kind='context', model='gemini-3.8-flash-low')
        target = self.repo / 'src/new_file.py'
        target.write_bytes(b'y' * 1000)
        self.assert_ok(self.call('agy', run='k-1', **kw))
        cal = self.check('k-1', 'packet_bytes')
        self.assertEqual((cal['limit'], cal['unit']), (2000000, 'bytes'))
        room = 2000000 - (cal['measured'] - 1000)
        target.write_bytes(b'y' * room)
        self.assert_ok(self.call('agy', run='k-2', **kw))
        self.assertEqual(self.check('k-2', 'packet_bytes')['measured'], 2000000)
        target.write_bytes(b'y' * (room + 1))
        proc = self.call('agy', run='k-3', **kw)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.callj(run='k-3')['failure'], 'input_too_large')
        self.assertEqual(self.check('k-3', 'packet_bytes')['measured'], 2000001)
        # Non-ASCII content: the packet limit counts bytes.
        target.write_bytes(self.utf8_text(room + 1).encode('utf-8'))
        proc = self.call('agy', run='k-4', **kw)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.check('k-4', 'packet_bytes')['measured'], 2000001)
        self.assertEqual(len(self.captures('agy', 'call')), 2)


# ======================================================== section 4 public release and B handoff doc

def fixture_files():
    return sorted(p for p in FIX.rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(FIX).parts)


def shipped_text_files():
    mapping = json.loads((REPO / 'tools/public-release-files.json').read_text(encoding='utf-8'))
    paths = {REPO / src for src in mapping.values()}
    paths.add(REPO / 'tests/test_cross_host.py')
    paths |= set(fixture_files())
    return sorted(p for p in paths if p.is_file() and p.suffix.lower() not in ('.zip', '.png', '.ico'))


class PublicRelease(CrossHostCase):
    def test_allowlist_never_contains_personal_run_records(self):
        mapping = json.loads((REPO / 'tools/public-release-files.json').read_text(encoding='utf-8'))
        for arc, src in mapping.items():
            for path in (arc, src):
                hits = [g for g in PERSONAL_RECORD_GLOBS if fnmatch.fnmatch(PurePosixPath(path).as_posix(), g)]
                self.assertEqual(hits, [], path)

    def test_fixtures_are_synthetic_and_have_no_record_names(self):
        for path in fixture_files():
            rel = path.relative_to(REPO).as_posix()
            self.assertEqual([g for g in PERSONAL_RECORD_GLOBS if fnmatch.fnmatch(rel, g)], [], rel)
            if path.suffix == '.json':
                def walk(node):
                    if isinstance(node, dict):
                        for k, v in node.items():
                            if re.fullmatch(r'(?i)(account|installation|user)_?id', k):
                                self.assertTrue(str(v).startswith('example-'), f'{rel}: {k}={v}')
                            walk(v)
                    elif isinstance(node, list):
                        for v in node:
                            walk(v)
                walk(json.loads(path.read_text(encoding='utf-8')))

    def test_shipped_files_have_only_synthetic_home_paths_and_uuids(self):
        for path in shipped_text_files():
            text = path.read_bytes().decode('utf-8', 'replace')
            rel = path.relative_to(REPO).as_posix()
            homes = [m.group(0) for m in HOME_RE.finditer(text) if m.group(1).lower() != 'example']
            self.assertEqual(homes, [], rel)
            uuids = [m.group(0) for m in UUID_RE.finditer(text) if not SYNTHETIC_UUID_RE.fullmatch(m.group(0))]
            self.assertEqual(uuids, [], rel)

    def test_shipped_files_contain_no_observed_private_identifier(self):
        source = os.environ.get('HGLOS_CROSS_HOST_PRIVATE_IDS')
        if not source:
            self.skipTest('HGLOS_CROSS_HOST_PRIVATE_IDS unset: required at C, values stay outside the repository')
        observed = [v.strip() for v in Path(source).read_text(encoding='utf-8').splitlines() if v.strip()]
        self.assertTrue(observed)
        for path in shipped_text_files():
            text = path.read_bytes().decode('utf-8', 'replace')
            self.assertEqual([v for v in observed if v in text], [], path.relative_to(REPO).as_posix())

class HandoffDocB(CrossHostCase):
    """Task B output. Expected to stay red after A until B writes the agent document."""

    def test_cross_host_validator_doc_handoff_guidance(self):
        doc = REPO / 'plugin/hglos/skills/hglos-verify/agents/cross-host-validator.md'
        self.assertTrue(doc.is_file(), str(doc))
        text = doc.read_text(encoding='utf-8')
        self.assertIn('../../../context-handoff.md', text)
        self.assertIn('cross_host.py', text)
        self.assertIn('validator.md', text)
        sys.path.insert(0, str(REPO / 'tools'))
        try:
            import package_skill
        finally:
            sys.path.pop(0)
        archive = package_skill.build('hglos-verify', package_skill.entries('hglos-verify'), self.tmp / 'archives')
        target = self.tmp / 'installed'
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        root = (target / 'hglos-verify').resolve()
        installed = root / 'agents/cross-host-validator.md'
        refs = re.findall(r'(?<![\w./\\-])((?:\.{1,2}/)*context-handoff\.md)(?![\w./\\-])',
                          installed.read_text(encoding='utf-8'))
        self.assertIn('../context-handoff.md', refs)
        self.assertEqual((installed.parent / '../context-handoff.md').resolve(), root / 'context-handoff.md')
        self.assertTrue((root / 'scripts/cross_host.py').is_file())


if __name__ == '__main__':
    unittest.main()
