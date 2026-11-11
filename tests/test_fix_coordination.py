"""doc/29 conditions 1-6: recoverable coordination after commits, stops, shells and reinstalls.

Hook inputs are synthesized (U5: no real Claude/Codex conversation). Each class names its condition.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/scripts'
sys.path.insert(0, str(SCRIPTS))
import coordination as c
import coordination_hook as hook
import setup_coordination as setup
from test_fix_plan_gate import unresolved_references

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
READS = ('Read', 'Glob', 'Grep', 'LS')


def run(script, *args, payload=None):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          input=json.dumps(payload) if payload is not None else None,
                          capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)


def control(root, *args):
    """A standalone coordination.py invocation exactly as the hook advertises it."""
    return setup.command([sys.executable, SCRIPTS / 'coordination.py', '--repo', root, *args], os.name == 'nt')


def context_of(output):
    return output.get('hookSpecificOutput', {}).get('additionalContext')


def denied(output):
    return output.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'


class HookCase(unittest.TestCase):
    """Two participants, A and B, driven through the real hook adapter process."""
    mode = 'scoped'
    host = 'claude'
    git_repo = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        if self.git_repo:
            if shutil.which('git') is None:
                self.skipTest('git is not installed')
            self.git('init', '-q', '-b', 'main')
            self.commit('README.md')
        c.initialize(self.root, self.mode)
        self.counter = 0

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=HGLOS test',
                                 '-c', 'user.email=hglos@example.invalid', '-c', 'commit.gpgsign=false',
                                 '-c', 'core.hooksPath=' + str(self.root / '.no-hooks'), *args],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, name):
        path = self.root / name
        path.write_text(f'{name} {getattr(self, "counter", 0)}\n', encoding='utf-8')
        self.git('add', '--', name)
        self.git('commit', '-q', '-m', 'commit ' + name)
        return self.git('rev-parse', 'HEAD')

    def sid(self, who, host=None):
        return hook.participant(host or self.host, {'session_id': 'external-' + who})

    def send(self, who, event, host=None, **fields):
        payload = {'session_id': 'external-' + who, 'cwd': str(self.root), 'hook_event_name': event, **fields}
        result = run('coordination_hook.py', '--repo', self.root, '--host', host or self.host, '--event', event,
                     payload=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def start(self, who, host=None):
        output = self.send(who, 'SessionStart', host, source='startup')
        self.assertNotIn('unavailable', json.dumps(output))
        return output

    def pre(self, who, tool, tool_input=None, tid=None, host=None):
        self.counter += 1
        tid = tid or f'{who}-tool-{self.counter}'
        output = self.send(who, 'PreToolUse', host, tool_name=tool, tool_input=tool_input or {}, tool_use_id=tid)
        return output, tid

    def post(self, who, tid, tool='Bash', event='PostToolUse', host=None):
        return self.send(who, event, host, tool_name=tool, tool_input={}, tool_use_id=tid, tool_response={})

    def edit(self, who, name, host=None):
        return self.pre(who, 'Edit', {'file_path': name, 'old_string': 'a', 'new_string': 'b'}, host=host)

    def row(self, who, host=None):
        with c.Registry(self.root) as reg:
            snap = reg.snapshot()
        return next(s for s in snap['sessions'] if s['id'] == self.sid(who, host))

    def events(self):
        with c.Registry(self.root) as reg:
            return [dict(r) for r in reg.db.execute('SELECT * FROM events ORDER BY seq')]


class H1HeadChange(HookCase):
    """Condition 1: same-branch HEAD advance continues; rewrites stop writes only; same-ID recovery."""
    git_repo = True

    def test_same_branch_commit_keeps_reads_and_writes_and_records_baseline_event(self):
        self.start('A')
        new_head = self.commit('external.txt')
        for tool in READS:
            output, _ = self.pre('A', tool, {'pattern': '*', 'file_path': 'README.md'})
            self.assertFalse(denied(output), output)
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output), output)
        self.post('A', tid, 'Edit')
        self.assertEqual(self.row('A')['baseline']['head'], new_head)
        self.assertTrue(any('baseline' in e['event'] and new_head in e['detail'] for e in self.events()),
                        'HEAD advance must leave an auditable baseline event naming the new HEAD')

    def test_branch_switch_denies_writes_only_and_points_to_rebaseline(self):
        self.start('A')
        self.git('checkout', '-q', '-b', 'feature')
        for tool in READS:
            output, _ = self.pre('A', tool, {'pattern': '*', 'file_path': 'README.md'})
            self.assertFalse(denied(output), (tool, output))
        output, _ = self.pre('A', 'Bash', {'command': control(self.root, 'status')})
        self.assertFalse(denied(output), output)
        output, _ = self.edit('A', 'A.cs')
        self.assertTrue(denied(output), output)
        self.assertIn('rebaseline', output['hookSpecificOutput']['permissionDecisionReason'])
        output, _ = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertTrue(denied(output), 'shell remains a writer after a branch switch')
        self.assertNotIn('unavailable', json.dumps(self.send('A', 'UserPromptSubmit', prompt='continue')))

    def test_reset_to_older_commit_recovers_with_rebaseline_and_same_session_id(self):
        self.commit('second.txt')
        self.start('A')
        sid = self.sid('A')
        self.git('reset', '-q', '--hard', 'HEAD~1')
        output, _ = self.edit('A', 'A.cs')
        self.assertTrue(denied(output), output)
        version = self.row('A')['version']
        refused = run('coordination.py', '--repo', self.root, 'rebaseline', '--session', sid, '--version', version)
        self.assertNotEqual(refused.returncode, 0, 'rebaseline must require --quiescent')
        stale = run('coordination.py', '--repo', self.root, 'rebaseline', '--session', sid,
                    '--version', version + 7, '--quiescent')
        self.assertNotEqual(stale.returncode, 0, 'stale version must be refused')
        command = control(self.root, 'rebaseline', '--session', sid, '--version', str(version), '--quiescent')
        output, _ = self.pre('A', 'Bash', {'command': command})
        self.assertFalse(denied(output), 'rebaseline must be reachable from inside the conversation')
        result = run('coordination.py', '--repo', self.root, 'rebaseline', '--session', sid,
                     '--version', version, '--quiescent')
        self.assertEqual(result.returncode, 0, result.stderr)
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output), output)
        self.post('A', tid, 'Edit')
        row = self.row('A')
        self.assertEqual(row['id'], sid)
        self.assertNotIn(row['state'], ('ended', 'recovered'))
        self.assertEqual(row['baseline']['head'], self.git('rev-parse', 'HEAD'))

    def test_amended_head_is_not_an_advance(self):
        self.commit('second.txt')
        self.start('A')
        self.git('commit', '-q', '--amend', '-m', 'rewritten')
        output, _ = self.pre('A', 'Read', {'file_path': 'README.md'})
        self.assertFalse(denied(output), output)
        output, _ = self.edit('A', 'A.cs')
        self.assertTrue(denied(output), output)

    def test_rebaseline_refuses_outstanding_operations(self):
        self.start('A')
        sid = self.sid('A')
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output), output)
        self.git('checkout', '-q', '-b', 'feature')
        version = self.row('A')['version']
        busy = run('coordination.py', '--repo', self.root, 'rebaseline', '--session', sid,
                   '--version', version, '--quiescent')
        self.assertNotEqual(busy.returncode, 0, 'a running tool call is not quiescent')
        self.post('A', tid, 'Edit')
        version = self.row('A')['version']
        done = run('coordination.py', '--repo', self.root, 'rebaseline', '--session', sid,
                   '--version', version, '--quiescent')
        self.assertEqual(done.returncode, 0, done.stderr)


class M1StopCleanup(HookCase):
    """Condition 2: a Pre without Post is cleaned at Stop/UserPromptSubmit, audited, and release works."""

    def abandoned_edit(self, event, **fields):
        self.start('A')
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output), output)
        stop = self.send('A', event, **fields)
        self.assertNotIn('decision', stop)
        self.assertIsNot(stop.get('continue'), True)
        row = self.row('A')
        self.assertEqual(row['operations'], [])
        self.assertEqual([(x['kind'], x['scope']) for x in row['claims']], [('file', 'a.cs')],
                         'file edit claims are retained (condition 3)')
        self.assertTrue(any(e['owner'] == row['id'] and tid in e['detail'] for e in self.events()),
                        'the abandoned tool_use_id must be recorded in an event')
        result = run('coordination.py', '--repo', self.root, 'release', '--session', row['id'],
                     '--version', row['version'], '--quiescent')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stop_clears_abandoned_operation_and_release_succeeds(self):
        self.abandoned_edit('Stop', stop_hook_active=False)

    def test_user_prompt_submit_clears_abandoned_operation(self):
        self.abandoned_edit('UserPromptSubmit', prompt='next request')

    def test_stop_clears_call_scoped_shell_claim(self):
        self.start('A')
        self.start('B')
        output, _ = self.pre('A', 'Bash', {'command': 'python build.py'})
        self.assertFalse(denied(output), output)
        output, _ = self.edit('B', 'B.cs')
        self.assertTrue(denied(output), 'shell holds the workspace during its call')
        self.send('A', 'Stop', stop_hook_active=False)
        output, _ = self.edit('B', 'B.cs')
        self.assertFalse(denied(output), output)

    def test_stop_cleans_only_the_stopping_session(self):
        self.start('A')
        self.start('B')
        self.assertFalse(denied(self.edit('A', 'A.cs')[0]))
        self.assertFalse(denied(self.edit('B', 'B.cs')[0]))
        self.send('A', 'Stop', stop_hook_active=False)
        self.assertEqual(self.row('A')['operations'], [])
        self.assertEqual(len(self.row('B')['operations']), 1)


class M2CallScopedShell(HookCase):
    """Condition 3: shell/unknown whole-workspace claims last only for the call."""

    def test_shell_claim_released_on_post(self):
        self.start('A')
        self.start('B')
        output, tid = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertFalse(denied(output), output)
        self.assertTrue(denied(self.edit('B', 'B.cs')[0]))
        self.post('A', tid)
        output, _ = self.edit('B', 'B.cs')
        self.assertFalse(denied(output), output)

    def test_unknown_tool_claim_released_on_post_failure(self):
        self.start('A')
        self.start('B')
        output, tid = self.pre('A', 'mcp__device__write', {'value': 1})
        self.assertFalse(denied(output), output)
        self.post('A', tid, 'mcp__device__write', event='PostToolUseFailure')
        output, _ = self.edit('B', 'B.cs')
        self.assertFalse(denied(output), output)

    def test_explicit_workspace_claim_survives_shell_call(self):
        self.start('A')
        self.start('B')
        with c.Registry(self.root) as reg:
            reg.claim(self.sid('A'), [], self.row('A')['version'])
        output, tid = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertFalse(denied(output), output)
        self.post('A', tid)
        self.assertTrue(denied(self.edit('B', 'B.cs')[0]), 'explicit claim must outlive the call')
        self.assertIn(('workspace', '.'), [(x['kind'], x['scope']) for x in self.row('A')['claims']])

    def test_explicit_file_claim_kept_after_shell_call(self):
        self.start('A')
        self.start('B')
        with c.Registry(self.root) as reg:
            reg.claim(self.sid('A'), [('file', 'A.cs')], self.row('A')['version'])
        output, tid = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertFalse(denied(output), output)
        self.post('A', tid)
        self.assertEqual([(x['kind'], x['scope']) for x in self.row('A')['claims']], [('file', 'a.cs')])
        self.assertTrue(denied(self.edit('B', 'A.cs')[0]))
        self.assertFalse(denied(self.edit('B', 'B.cs')[0]))

    def test_file_edit_claim_survives_post(self):
        self.start('A')
        self.start('B')
        output, tid = self.edit('A', 'A.cs')
        self.post('A', tid, 'Edit')
        self.assertTrue(denied(self.edit('B', 'A.cs')[0]))
        self.assertFalse(denied(self.edit('B', 'B.cs')[0]))

    def test_shell_overlapping_peer_claim_still_denied(self):
        self.start('A')
        self.start('B')
        self.assertFalse(denied(self.edit('B', 'A.cs')[0]))
        output, _ = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertTrue(denied(output))


class M2ExclusiveMode(HookCase):
    mode = 'exclusive'

    def test_shell_is_call_scoped_but_edit_holds_workspace(self):
        self.start('A')
        self.start('B')
        output, tid = self.pre('A', 'Bash', {'command': 'git status'})
        self.assertFalse(denied(output), output)
        self.post('A', tid)
        output, tid = self.pre('B', 'Bash', {'command': 'git status'})
        self.assertFalse(denied(output), output)
        self.post('B', tid)
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output), output)
        self.post('A', tid, 'Edit')
        self.assertTrue(denied(self.pre('B', 'Bash', {'command': 'git status'})[0]))


class M3ContextBudget(HookCase):
    """Condition 4: context only on SessionStart, denial or peer/claim change; at most 1,000 chars."""

    def test_session_start_summary_is_bounded_for_both_hosts(self):
        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                text = context_of(self.start('A', host))
                self.assertTrue(text)
                self.assertLessEqual(len(text), 1000)

    def test_session_start_bounded_with_many_long_peers(self):
        with c.Registry(self.root) as reg:
            for index in range(14):
                peer = f'peer-{index}'
                reg.register('codex', 'x' * 2000, peer)
                reg.claim(peer, [('file', f'src/{"long-name-" * 8}{index}.cs')], 1)
        text = context_of(self.start('A'))
        self.assertTrue(text)
        self.assertLessEqual(len(text), 1000)

    def test_unchanged_allowed_calls_have_no_context(self):
        self.start('A')
        output, tid = self.edit('A', 'A.cs')
        self.post('A', tid, 'Edit')
        output, tid = self.edit('A', 'A.cs')
        self.assertFalse(denied(output))
        self.assertFalse(context_of(output), output)
        self.assertFalse(context_of(self.post('A', tid, 'Edit')))
        output, _ = self.pre('A', 'Read', {'file_path': 'A.cs'})
        self.assertFalse(context_of(output), output)
        self.assertFalse(context_of(self.send('A', 'UserPromptSubmit', prompt='next')))

    def test_peer_change_is_reported_once_and_bounded(self):
        self.start('A')
        output, _ = self.pre('A', 'Read', {'file_path': 'A.cs'})
        self.assertFalse(context_of(output))
        self.start('B')
        self.assertFalse(denied(self.edit('B', 'B.cs')[0]))
        output, _ = self.pre('A', 'Read', {'file_path': 'A.cs'})
        text = context_of(output)
        self.assertTrue(text, 'peer registration/claim is a change worth reporting')
        self.assertLessEqual(len(text), 1000)
        output, _ = self.pre('A', 'Read', {'file_path': 'A.cs'})
        self.assertFalse(context_of(output), 'the same change is not re-sent')

    def test_denial_reason_is_bounded(self):
        self.start('A')
        with c.Registry(self.root) as reg:
            reg.register('codex', 'y' * 2000, 'peer')
            reg.claim('peer', [('file', 'A.cs')], 1)
        output, _ = self.edit('A', 'A.cs')
        self.assertTrue(denied(output))
        specific = output['hookSpecificOutput']
        self.assertTrue(specific['permissionDecisionReason'])
        self.assertLessEqual(len(specific['permissionDecisionReason']), 1000)
        self.assertLessEqual(len(specific.get('additionalContext') or ''), 1000)


class L1ReadsAreObservations(HookCase):
    """Condition 5: Read/Glob/Grep/LS leave no state change and no outstanding operation."""

    def test_read_tools_do_not_change_state_or_operations(self):
        self.start('A')
        before = self.row('A')
        for tool in READS:
            output, _ = self.pre('A', tool, {'pattern': '*', 'file_path': 'A.cs', 'path': '.'})
            self.assertFalse(denied(output), output)
        after = self.row('A')
        self.assertEqual(after['state'], before['state'])
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['operations'], [])
        self.assertEqual(after['claims'], [])

    def test_reads_without_post_do_not_block_release(self):
        self.start('A')
        for tool in READS:
            self.pre('A', tool, {'pattern': '*'})
        row = self.row('A')
        result = run('coordination.py', '--repo', self.root, 'release', '--session', row['id'],
                     '--version', row['version'], '--quiescent')
        self.assertEqual(result.returncode, 0, result.stderr)


def find_bash():
    if os.name != 'nt':
        return shutil.which('bash')
    git = shutil.which('git')
    if git:
        # Git for Windows: <Git>/cmd/git.exe or <Git>/mingw64/bin/git.exe; Git Bash is <Git>/bin/bash.exe.
        for folder in list(Path(git).resolve().parents)[:3]:
            candidate = folder / 'bin/bash.exe'
            if candidate.is_file():
                return str(candidate)
    return None


class M7PinnedHookCopies(unittest.TestCase):
    """Condition 6: hooks call a hashed .hglos/bin copy; missing copy blocks only PreToolUse."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bin = self.root / '.hglos/bin'

    def setup(self, *extra, host='both'):
        return run('setup_coordination.py', '--repo', self.root, '--host', host, '--mode', 'scoped', *extra)

    def handlers(self):
        found = []
        for name in ('.claude/settings.local.json', '.codex/hooks.json'):
            data = json.loads((self.root / name).read_text(encoding='utf-8'))
            for event, groups in data['hooks'].items():
                for group in groups:
                    for handler in group['hooks']:
                        if 'coordination_hook.py' in handler.get('command', ''):
                            found.append((name, event, handler))
        return found

    @staticmethod
    def windows_command(handler):
        encoded = handler['commandWindows'].rsplit(' ', 1)[1]
        return base64.b64decode(encoded).decode('utf-16-le')

    def test_apply_installs_byte_identical_copies_with_hash_record(self):
        result = self.setup('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.bin / 'manifest.json').read_text(encoding='utf-8'))
        for name in ('coordination_hook.py', 'coordination.py'):
            copy = self.bin / name
            self.assertEqual(copy.read_bytes(), (SCRIPTS / name).read_bytes())
            self.assertEqual(manifest[name], hashlib.sha256(copy.read_bytes()).hexdigest())

    def test_hook_commands_reference_pinned_copy_not_skill_path(self):
        self.assertEqual(self.setup('--apply').returncode, 0)
        pinned = str(self.bin / 'coordination_hook.py')
        handlers = self.handlers()
        self.assertTrue(handlers)
        for name, event, handler in handlers:
            commands = [handler['command']] + ([self.windows_command(handler)] if 'commandWindows' in handler else [])
            for command in commands:
                with self.subTest(file=name, event=event):
                    self.assertIn(pinned, command)
                    self.assertNotIn(str(SCRIPTS), command)

    def execute(self, name, handler, shell, payload):
        if shell == 'windows':
            argv = handler['commandWindows'].split()
        elif shell == 'powershell':
            exe = shutil.which('pwsh') or shutil.which('powershell')
            if exe is None:
                self.skipTest('PowerShell not available')
            argv = [exe, '-NoProfile', '-NonInteractive', '-Command', handler['command']]
        else:
            bash = find_bash()
            if bash is None:
                self.skipTest('bash not available')
            argv = [bash, '-c', handler['command']]
        return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=ENV, timeout=60)

    def shells(self, name, handler):
        if name.startswith('.codex'):
            return ['bash'] + (['windows'] if os.name == 'nt' else [])
        return ['powershell' if handler.get('shell') == 'powershell' else 'bash']

    def test_generated_commands_run_with_copy_and_missing_copy_blocks_only_pre_tool_use(self):
        self.assertEqual(self.setup('--apply').returncode, 0)
        base = {'session_id': 'generated-command', 'cwd': str(self.root), 'tool_use_id': 't1',
                'tool_name': 'Edit', 'tool_input': {'file_path': 'A.cs'}}
        for name, event, handler in self.handlers():
            if event != 'SessionStart':
                continue
            for shell in self.shells(name, handler):
                with self.subTest(file=name, shell=shell, copy='present'):
                    result = self.execute(name, handler, shell, dict(base, hook_event_name=event))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    json.loads(result.stdout)
        (self.bin / 'coordination_hook.py').unlink(missing_ok=True)
        for index, (name, event, handler) in enumerate(self.handlers()):
            for shell in self.shells(name, handler):
                # A registered owner editing its own file: only the missing copy may cause a block.
                external = f'missing-{index}-{shell}'
                host = 'codex' if name.startswith('.codex') else 'claude'
                with c.Registry(self.root) as reg:
                    reg.register(host, sid=hook.participant(host, {'session_id': external}))
                payload = dict(base, session_id=external, hook_event_name=event,
                               tool_input={'file_path': f'{index}-{shell}.cs'})
                with self.subTest(file=name, event=event, shell=shell, copy='missing'):
                    result = self.execute(name, handler, shell, payload)
                    if event == 'PreToolUse':
                        blocked = result.returncode != 0 or '"deny"' in result.stdout
                        self.assertTrue(blocked, 'missing hook copy must not silently allow a tool call')
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_hglos_hook_entries_replaced_after_preview(self):
        settings = self.root / '.claude/settings.local.json'
        settings.parent.mkdir()
        user_hook = {'matcher': 'Read', 'hooks': [{'type': 'command', 'command': 'echo keep'}]}
        old = {}
        for event in ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure',
                      'Stop', 'SessionEnd'):
            command = (f'python /old-install-marker/hglos-coordinate/scripts/coordination_hook.py '
                       f'--repo {self.root.as_posix()} --host claude --event {event}')
            old[event] = [{'hooks': [{'type': 'command', 'command': command, 'timeout': 30}]}]
        old['PreToolUse'].insert(0, user_hook)
        settings.write_text(json.dumps({'permissions': {'deny': ['Bash(rm *)']}, 'hooks': old}, indent=2),
                            encoding='utf-8')
        original = settings.read_bytes()
        preview = self.setup(host='claude')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(settings.read_bytes(), original)
        self.assertIn('old-install-marker', preview.stdout, 'preview must show what will be replaced')
        applied = self.setup('--apply', host='claude')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        merged = json.loads(settings.read_text(encoding='utf-8'))
        self.assertEqual(merged['permissions'], {'deny': ['Bash(rm *)']})
        self.assertIn(user_hook, merged['hooks']['PreToolUse'])
        self.assertNotIn('old-install-marker', settings.read_text(encoding='utf-8'))
        for event, groups in merged['hooks'].items():
            ours = [h for g in groups for h in g['hooks'] if 'coordination_hook.py' in h.get('command', '')]
            self.assertEqual(len(ours), 1, event)
        before = settings.read_bytes()
        self.assertEqual(self.setup('--apply', host='claude').returncode, 0)
        self.assertEqual(settings.read_bytes(), before)


SCHEMA1 = '''
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, host TEXT NOT NULL, task TEXT NOT NULL,
    state TEXT NOT NULL, reason TEXT NOT NULL, version INTEGER NOT NULL,
    baseline TEXT NOT NULL, started TEXT NOT NULL, seen REAL NOT NULL);
CREATE TABLE claims (
    owner TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL,
    scope TEXT NOT NULL, UNIQUE(owner,kind,scope));
CREATE TABLE operations (
    owner TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL,
    tool TEXT NOT NULL, started TEXT NOT NULL, UNIQUE(owner,id));
CREATE TABLE handoffs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id),
    artifact TEXT NOT NULL, sha256 TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, event TEXT NOT NULL,
    detail TEXT NOT NULL, created TEXT NOT NULL);
PRAGMA user_version = 1;
'''


class Schema1Migration(unittest.TestCase):
    """Preservation (doc/29 section 4): a schema 1 registry moves to schema 2 without loss."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        folder = self.root / '.hglos'
        (folder / 'runtime').mkdir(parents=True)
        (folder / '.gitignore').write_text('runtime/\ncoordination.json\n', encoding='utf-8')
        (folder / 'coordination.json').write_text(json.dumps(
            {'schema': 1, 'mode': 'scoped', 'root_id': c.root_identity(self.root)}, indent=2) + '\n', encoding='utf-8')
        db = sqlite3.connect(folder / 'runtime/coordination.sqlite3')
        db.executescript(SCHEMA1)
        rows = [('a', 'claude', 'engine', 'working', '', 3, 'null', '2026-10-01T00:00:00+00:00', 1.0),
                ('b', 'codex', 'ui', 'ended', '', 1, 'null', '2026-10-01T00:00:00+00:00', 2.0)]
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)', rows)
        self.claims = {('a', 'file', 'src/a.cs'), ('a', 'tree', 'src/engine'), ('b', 'resource', 'build')}
        db.executemany('INSERT INTO claims VALUES (?,?,?)', sorted(self.claims))
        db.execute("INSERT INTO handoffs VALUES ('h1','a','result.md','%s','note','2026-10-01T00:00:00+00:00')" % ('0' * 64))
        db.execute("INSERT INTO events(owner,event,detail,created) VALUES ('a','claim','[]','2026-10-01T00:00:00+00:00')")
        db.commit()
        db.close()

    def test_open_migrates_to_schema_2_preserving_rows(self):
        with c.Registry(self.root) as reg:
            snap = reg.snapshot()
            version = reg.db.execute('PRAGMA user_version').fetchone()[0]
        self.assertEqual(version, 2)
        self.assertEqual({(s['id'], s['state'], s['version']) for s in snap['sessions']},
                         {('a', 'working', 3), ('b', 'ended', 1)})
        claims = {(x['owner'], x['kind'], x['scope']) for s in snap['sessions'] for x in s['claims']}
        self.assertEqual(claims, self.claims)
        self.assertEqual([h['id'] for h in snap['handoffs']], ['h1'])
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='new')
            with self.assertRaises(c.Conflict):
                reg.claim('new', [('file', 'src/engine/x.cs')], 1)

    def test_setup_rerun_on_legacy_project_succeeds(self):
        result = run('setup_coordination.py', '--repo', self.root, '--host', 'claude', '--mode', 'scoped', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        with c.Registry(self.root) as reg:
            self.assertEqual(len(reg.snapshot()['sessions']), 2)


class CoordinateSkillPaths(unittest.TestCase):
    """Condition 11 (L7) for the hglos-coordinate skill owned by task A."""

    def test_coordinate_skill_references_resolve_in_plugin_and_installed_layouts(self):
        self.assertEqual(unresolved_references('hglos-coordinate'), [])


if __name__ == '__main__':
    unittest.main()
