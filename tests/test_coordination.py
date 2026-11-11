"""Behavioral invariants for local cooperative coordination and host adapters."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/scripts'
sys.path.insert(0, str(SCRIPTS))
import coordination as c
import coordination_hook as hook
import setup_coordination as setup
import coordination_view as view


def run(script, *args, payload=None):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          input=json.dumps(payload) if payload is not None else None,
                          capture_output=True, text=True, encoding='utf-8',
                          env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'), timeout=30)


class Coordination(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        c.initialize(self.root, 'scoped')
        self.reg = c.Registry(self.root)
        self.addCleanup(self.reg.db.close)
        self.reg.register('claude', 'engine work', 'a')
        self.reg.register('codex', 'UI work', 'b')

    def test_overlapping_claims_and_future_files(self):
        self.reg.claim('a', [('tree', 'src/engine')], 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('file', 'src/engine/new.cs')], 1)
        self.reg.claim('b', [('file', 'src/ui/View.cs')], 1)
        self.assertEqual(len(self.reg.snapshot()['sessions'][1]['claims']), 1)

    def test_case_folded_and_dot_path_collision(self):
        self.reg.claim('a', [('file', 'src/../Engine.CS')], 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('file', 'engine.cs')], 1)

    def test_failed_extension_is_all_or_nothing_and_retains_original(self):
        self.reg.claim('a', [('file', 'A.cs')], 1)
        self.reg.claim('b', [('file', 'B.cs')], 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('a', [('file', 'B.cs'), ('file', 'C.cs')], 1)
        actual = list(self.reg.db.execute('SELECT scope FROM claims WHERE owner="a"'))
        self.assertEqual([r[0] for r in actual], ['a.cs'])

    def test_resources_conflict_and_workspace_blocks_disjoint_scopes(self):
        self.reg.claim('a', [('file', 'A.cs'), ('resource', 'build')], 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('resource', 'BUILD')], 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('workspace', '.')], 1)

    def test_no_expiry_even_when_ended(self):
        self.reg.claim('a', [('file', 'A.cs')], 1)
        self.reg.db.execute('UPDATE sessions SET seen=? WHERE id="a"', (time.time() - 3600,))
        self.assertTrue(next(s for s in self.reg.snapshot()['sessions'] if s['id'] == 'a')['suspect'])
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('file', 'A.cs')], 1)
        self.reg.end('a', 1)
        with self.assertRaises(c.Conflict):
            self.reg.claim('b', [('file', 'A.cs')], 1)

    def test_release_requires_quiescence_and_finished_operations(self):
        self.reg.before('a', 'tool-1', 'Bash', [('workspace', '.')])
        with self.assertRaises(c.CoordinationError):
            self.reg.release('a', 1)
        with self.assertRaises(c.CoordinationError):
            self.reg.release('a', 1, True)
        self.reg.after('a', 'tool-1')
        self.assertEqual(self.reg.release('a', 1, True)['version'], 2)
        self.reg.claim('a', [('file', 'new.cs')], 2)
        with self.assertRaises(c.CoordinationError):
            self.reg.release('a', 1, True)
        self.assertTrue(self.reg.snapshot()['sessions'][0]['claims'])

    def test_recovery_invalidates_old_owner_and_does_not_touch_new_owner(self):
        self.reg.before('a', 'tool-1', 'Bash', [('workspace', '.')])
        with self.assertRaises(c.CoordinationError):
            self.reg.recover('a', 1, 'no confirmation')
        self.reg.recover('a', 1, 'operator stopped all child processes', True)
        self.reg.claim('b', [('workspace', '.')], 1)
        for action in (lambda: self.reg.register('claude', sid='a'),
                       lambda: self.reg.heartbeat('a'),
                       lambda: self.reg.claim('a', [('workspace', '.')], 1),
                       lambda: self.reg.release('a', 1, True)):
            with self.assertRaises(c.CoordinationError):
                action()
        self.assertEqual(len(self.reg.snapshot()['sessions'][1]['claims']), 1)

    def test_baseline_change_stops_writes(self):
        # doc/29 H1: a non-advance baseline change still stops writes, and the error names the
        # in-conversation recovery (rebaseline) instead of demanding a new session ID.
        with patch.object(c, 'baseline', return_value={'head': 'changed'}):
            with self.assertRaisesRegex(c.CoordinationError, 'rebaseline'):
                self.reg.claim('a', [('file', 'A.cs')], 1)
        self.assertEqual(self.reg.db.execute('SELECT count(*) FROM claims').fetchone()[0], 0)

    def test_conflict_audit_cannot_resurrect_ended_or_recovered_owner(self):
        self.reg.claim('b', [('workspace', '.')], 1)
        original = self.reg.record_conflict
        for index, terminal in enumerate(('ended', 'recovered')):
            for entrypoint in ('claim', 'before'):
                sid = f'victim-{index}-{entrypoint}'
                self.reg.register('claude', sid=sid)
                def interleave(owner, version, error):
                    if terminal == 'ended':
                        self.reg.end(owner, version)
                    else:
                        self.reg.recover(owner, version, 'operator verified termination', True)
                    original(owner, version, error)
                with patch.object(self.reg, 'record_conflict', side_effect=interleave):
                    with self.assertRaises(c.Conflict):
                        if entrypoint == 'claim':
                            self.reg.claim(sid, [('file', 'A.cs')], 1)
                        else:
                            self.reg.before(sid, 'old-operation', 'Write', [('file', 'A.cs')])
                row = self.reg.db.execute('SELECT state FROM sessions WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['state'], terminal)
                with self.assertRaises(c.CoordinationError):
                    self.reg.session(sid)

    def test_nested_cwd_finds_same_root_and_rejects_nested_init(self):
        child = self.root / 'src/deep'
        child.mkdir(parents=True)
        with c.Registry(child) as other:
            self.assertEqual(other.root, self.root)
            self.assertEqual(other.snapshot()['root_id'], self.reg.snapshot()['root_id'])
        with self.assertRaises(c.CoordinationError):
            c.initialize(child)

    def test_claims_reject_escape_glob_metadata_and_wrong_type(self):
        (self.root / 'src').mkdir()
        for kind, path in [('file', '../outside.cs'), ('file', 'src/*.cs'),
                           ('file', '.git/config'), ('tree', '.hglos'), ('file', 'src')]:
            with self.subTest(path=path), self.assertRaises(c.CoordinationError):
                self.reg.claim('a', [(kind, path)], 1)

    def test_windows_dot_space_stream_and_device_aliases_rejected(self):
        source = self.root / 'A.cs'
        source.write_text('unchanged', encoding='utf-8')
        self.reg.claim('a', [('file', 'A.cs')], 1)
        if os.name == 'nt':
            self.assertTrue(source.samefile(self.root / 'A.cs.'))
        for name in ('A.cs.', 'A.cs ', 'A.cs:stream', 'NUL.cs', 'COM1', 'src./A.cs'):
            with self.subTest(name=name), self.assertRaises(c.CoordinationError):
                self.reg.claim('b', [('file', name)], 1)
        self.assertEqual(source.read_text(), 'unchanged')

    def test_handoff_hash_detects_later_changes_without_approving_content(self):
        path = self.root / 'result.md'
        path.write_text('not runtime verified', encoding='utf-8')
        item = self.reg.handoff('a', 'result.md', 'pending verification')
        self.assertTrue(self.reg.verify_handoff(item['id'])['matches_current_bytes'])
        path.write_text('changed', encoding='utf-8')
        self.assertFalse(self.reg.verify_handoff(item['id'])['matches_current_bytes'])

    def test_transaction_rollback_has_no_partial_claim(self):
        with self.assertRaises(RuntimeError):
            with self.reg.transaction():
                self.reg._claim('a', [('file', 'A.cs')], 1)
                raise RuntimeError('simulated interrupted transaction')
        self.assertEqual(self.reg.db.execute('SELECT count(*) FROM claims').fetchone()[0], 0)

    def test_simultaneous_process_claim_has_exactly_one_winner(self):
        gate = self.root / 'gate'
        code = ("import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "import coordination as c;root=Path(sys.argv[2]);sid=sys.argv[3];gate=Path(sys.argv[4]);\n"
                "while not gate.exists(): time.sleep(.01)\n"
                "with c.Registry(root) as r:\n"
                " try: r.claim(sid,[('tree','future')],1);print('won')\n"
                " except c.Conflict: print('conflict')\n")
        processes = [subprocess.Popen([sys.executable, '-c', code, str(SCRIPTS), str(self.root), sid, str(gate)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for sid in ('a', 'b')]
        try:
            gate.touch()
            results = [p.communicate(timeout=20) for p in processes]
            self.assertEqual([p.returncode for p in processes], [0, 0], results)
            self.assertEqual(sorted(out.strip() for out, _ in results), ['conflict', 'won'])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_database_busy_is_error_not_empty_success(self):
        self.reg.db.execute('BEGIN IMMEDIATE')
        try:
            with c.Registry(self.root) as other:
                other.db.execute('PRAGMA busy_timeout=1')
                with self.assertRaises(sqlite3.OperationalError):
                    other.claim('b', [('file', 'new.cs')], 1)
        finally:
            self.reg.db.execute('ROLLBACK')

    def test_symlink_path_rejected_when_platform_permits_creation(self):
        target = self.root / 'real'
        target.mkdir()
        alias = self.root / 'alias'
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Platform/sandbox denies real symlink creation: {exc}')
        with self.assertRaises(c.CoordinationError):
            self.reg.claim('a', [('file', 'alias/future.cs')], 1)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        c.initialize(self.root, 'scoped')
        self.payload = {'session_id': 'host-session', 'cwd': str(self.root), 'tool_use_id': 'tool-1',
                        'tool_name': 'Write', 'tool_input': {'file_path': 'A.cs'}}
        hook.handle('codex', 'SessionStart', self.root, self.payload)
        self.sid = hook.participant('codex', self.payload)

    def invoke(self, event, payload=None):
        result = run('coordination_hook.py', '--repo', self.root, '--host', 'codex', '--event', event,
                     payload=self.payload if payload is None else payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout).get('hookSpecificOutput', {})

    def test_same_external_id_is_namespaced_by_host(self):
        self.assertNotEqual(self.sid, hook.participant('claude', self.payload))

    def test_lifecycle_observers_do_not_request_continuation(self):
        for host in ('codex', 'claude'):
            if host == 'claude':
                hook.handle(host, 'SessionStart', self.root, self.payload)
            for event in ('Stop', 'Interrupt', 'SessionEnd'):
                with self.subTest(host=host, event=event):
                    self.assertEqual(hook.handle(host, event, self.root, self.payload), {})
        for event in ('Stop', 'Interrupt', 'SessionEnd'):
            error = hook.output(event, 'observer error')
            self.assertEqual(error, {'systemMessage': 'observer error'})

    def test_file_conflict_returns_explicit_deny_never_allow(self):
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='other')
            reg.claim('other', [('file', 'A.cs')], 1)
        result = self.invoke('PreToolUse')
        self.assertEqual(result['permissionDecision'], 'deny')
        self.assertIn('Claim conflict', result['permissionDecisionReason'])
        with c.Registry(self.root) as reg:
            self.assertEqual(reg.session(self.sid)['state'], 'blocked')

    def test_disjoint_edit_tracks_operation_until_post(self):
        result = self.invoke('PreToolUse')
        self.assertNotIn('permissionDecision', result)
        with c.Registry(self.root) as reg:
            self.assertEqual(len(reg.snapshot()['sessions'][0]['operations']), 1)
        self.invoke('PostToolUse')
        with c.Registry(self.root) as reg:
            self.assertEqual(len(reg.snapshot()['sessions'][0]['operations']), 0)
            self.assertEqual(len(reg.snapshot()['sessions'][0]['claims']), 1)

    def test_shell_and_unknown_mcp_require_workspace_even_if_command_looks_read_only(self):
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='other')
            reg.claim('other', [('file', 'A.cs')], 1)
        for name in ('Bash', 'PowerShell', 'mcp__unknown__read'):
            payload = dict(self.payload, tool_name=name, tool_input={'command': 'git status'})
            self.assertEqual(self.invoke('PreToolUse', payload)['permissionDecision'], 'deny')

    def test_known_read_does_not_conflict_with_writer(self):
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='other')
            reg.claim('other', [('workspace', '.')], 1)
        payload = dict(self.payload, tool_name='Read')
        self.assertNotIn('permissionDecision', self.invoke('PreToolUse', payload))

    def test_apply_patch_move_claims_both_paths_and_rejects_escape(self):
        payload = dict(self.payload, tool_name='apply_patch', tool_input={
            'command': '*** Begin Patch\n*** Update File: A.cs\n*** Move to: B.cs\n@@\n-old\n+new\n*** End Patch'})
        self.assertNotIn('permissionDecision', self.invoke('PreToolUse', payload))
        with c.Registry(self.root) as reg:
            scopes = [c['scope'] for c in reg.snapshot()['sessions'][0]['claims']]
            self.assertEqual(scopes, ['a.cs', 'b.cs'])
        payload['tool_input']['command'] = '*** Add File: ../escape.cs\n+x'
        self.assertEqual(self.invoke('PreToolUse', payload)['permissionDecision'], 'deny')

    def test_control_call_cannot_chain_commands_or_operate_on_other_owner(self):
        command = setup.command([sys.executable, SCRIPTS / 'coordination.py', '--repo', self.root,
                                 'release', '--session', self.sid, '--version', '1', '--quiescent'], os.name == 'nt')
        self.assertTrue(hook.control_call(command, self.root, self.sid))
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='other')
            reg.claim('other', [('file', 'other.cs')], 1)
        payload = dict(self.payload, tool_name='PowerShell', tool_input={'command': command})
        self.assertNotIn('permissionDecision', self.invoke('PreToolUse', payload))
        for bad in (command + '; echo bad', command.replace(self.sid, 'other'), command.replace('release', 'recover'),
                    command.replace('coordination.py', 'something_else.py')):
            self.assertFalse(hook.control_call(bad, self.root, self.sid))
        expression = setup.command([sys.executable, SCRIPTS / 'coordination.py', '--repo', self.root,
                                    'register', '--session', self.sid, '--host', 'codex'], os.name == 'nt')
        expression += " --task ([IO.File]::WriteAllText('A.cs','x'))"
        self.assertFalse(hook.control_call(expression, self.root, self.sid))
        with c.Registry(self.root) as reg:
            reg.register('claude', sid='peer')
            reg.claim('peer', [('file', 'A.cs')], 1)
        payload = dict(self.payload, tool_name='Bash', tool_input={'command': expression})
        self.assertEqual(self.invoke('PreToolUse', payload)['permissionDecision'], 'deny')

    @unittest.skipUnless(os.name == 'nt', 'PowerShell literal invocation grammar')
    def test_powershell_literal_quotes_apostrophes_and_path_executable(self):
        parts = [sys.executable, str(SCRIPTS / 'coordination.py'), '--repo', str(self.root), 'status']
        double_quoted = '& ' + ' '.join('"' + token + '"' for token in parts)
        self.assertTrue(hook.control_call(double_quoted, self.root, self.sid))
        apostrophe_root = self.root / "O'Brien"
        parts[3] = str(apostrophe_root)
        single_quoted = setup.command(parts, True)
        self.assertTrue(hook.control_call(single_quoted, apostrophe_root, self.sid))
        parts[0] = 'python'
        with patch.object(hook.shutil, 'which', return_value=sys.executable):
            self.assertTrue(hook.control_call(setup.command(parts, True), apostrophe_root, self.sid))
        for expr in ('([IO.File]::WriteAllText("A.cs","x"))', '{Write-Output bad}', '@(bad)'):
            self.assertIsNone(hook.literal_tokens('python --task ' + expr))

    def test_corrupt_registry_and_bad_payload_return_deny(self):
        for cwd in (None, 123, False):
            invalid = dict(self.payload, cwd=cwd)
            self.assertEqual(self.invoke('PreToolUse', invalid)['permissionDecision'], 'deny')
        bad = self.invoke('PreToolUse', {'session_id': 'host-session', 'cwd': str(self.root)})
        self.assertEqual(bad['permissionDecision'], 'deny')
        (self.root / '.hglos/coordination.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.invoke('PreToolUse')['permissionDecision'], 'deny')

    def test_session_end_retains_claim_and_does_not_infer_completion(self):
        self.invoke('PreToolUse')
        self.invoke('PostToolUse')
        self.invoke('SessionEnd')
        with c.Registry(self.root) as reg:
            item = reg.snapshot()['sessions'][0]
            self.assertEqual(item['state'], 'ended')
            self.assertEqual(len(item['claims']), 1)
        self.assertEqual(self.invoke('PreToolUse')['permissionDecision'], 'deny')

    def test_clean_ended_session_can_resume_but_residual_claim_cannot(self):
        self.invoke('SessionEnd')
        self.invoke('SessionStart')
        with c.Registry(self.root) as reg:
            self.assertEqual(reg.session(self.sid)['version'], 2)
        self.invoke('PreToolUse')
        self.invoke('PostToolUse')
        self.invoke('SessionEnd')
        response = self.invoke('SessionStart')
        self.assertIn('retains claims', response['additionalContext'])
        self.assertEqual(self.invoke('PreToolUse')['permissionDecision'], 'deny')


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_preview_writes_nothing_and_apply_is_idempotent_preserves_user_settings(self):
        args = ('--repo', self.root, '--host', 'both', '--mode', 'scoped')
        self.assertEqual(run('setup_coordination.py', *args).returncode, 0)
        self.assertEqual(list(self.root.iterdir()), [])
        (self.root / '.claude').mkdir()
        settings = self.root / '.claude/settings.local.json'
        original_hook = {'matcher': 'Read', 'hooks': [{'type': 'command', 'command': 'echo keep'}]}
        settings.write_text(json.dumps({'permissions': {'deny': ['Bash(rm *)']},
                                        'hooks': {'PreToolUse': [original_hook]}}), encoding='utf-8')
        agents = self.root / 'AGENTS.md'
        agents.write_bytes(b'# Existing\r\nPreserve this text.\r\n')
        first = run('setup_coordination.py', *args, '--apply')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual((self.root / '.hglos/RULES.md').read_text(encoding='utf-8'),
                         (REPO / 'plugin/hglos/RULES.md').read_text(encoding='utf-8'))
        self.assertTrue(agents.read_bytes().startswith(b'# Existing\r\nPreserve this text.\r\n'))
        merged = json.loads(settings.read_text())
        self.assertEqual(merged['permissions'], {'deny': ['Bash(rm *)']})
        self.assertEqual(merged['hooks']['PreToolUse'][0], original_hook)
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        second = run('setup_coordination.py', *args, '--apply')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_conflicting_policy_or_mode_preserved(self):
        folder = self.root / '.hglos'
        folder.mkdir()
        policy = folder / 'COORDINATION.md'
        policy.write_text('user policy', encoding='utf-8')
        result = run('setup_coordination.py', '--repo', self.root, '--apply')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(policy.read_text(), 'user policy')
        self.assertFalse((self.root / 'AGENTS.md').exists())

    def test_exclusive_default_denies_scoped_claims(self):
        result = run('setup_coordination.py', '--repo', self.root, '--host', 'codex', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        with c.Registry(self.root) as reg:
            reg.register('codex', sid='one')
            with self.assertRaises(c.CoordinationError):
                reg.claim('one', [('file', 'A.cs')], 1)
            reg.claim('one', [('workspace', '.')], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows generated launcher integration')
    def test_generated_codex_windows_command_executes_adapter(self):
        # doc/29 M7: generated hooks call the pinned .hglos/bin copy, which exists only after --apply.
        applied = run('setup_coordination.py', '--repo', self.root, '--host', 'codex', '--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        definition = json.loads((self.root / '.codex/hooks.json').read_text(encoding='utf-8'))['hooks']
        command = definition['SessionStart'][0]['hooks'][0]['commandWindows']
        result = subprocess.run(command.split(), input=json.dumps({'session_id': 'windows-launch',
                                                                 'cwd': str(self.root), 'hook_event_name': 'SessionStart'}),
                                text=True, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('HGLOS cooperative coordination', context)


class SessionViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        c.initialize(self.root, 'scoped')
        with c.Registry(self.root) as reg:
            reg.register('codex', '<script>bad</script> | task', 'a')
            reg.claim('a', [('file', 'A.cs')], 1)
            self.snapshot = reg.snapshot()
        self.path = self.root / '.hglos/runtime/sessions.json'
        self.path.write_text(json.dumps(self.snapshot), encoding='utf-8')

    def test_view_escapes_untrusted_data_and_exposes_age_and_claims(self):
        loaded = view.load_sessions(self.root, self.path)
        html = view.sessions_html(loaded)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('file:a.cs', html)
        self.assertIn(loaded['sha256'], html)
        self.assertIn('수집 후', html)
        self.assertIn('\\| task', view.sessions_markdown(loaded))

    def test_snapshot_from_different_root_or_invalid_rows_rejected(self):
        for invalid in (dict(self.snapshot, root=str(self.root.parent)),
                        dict(self.snapshot, sessions=[{'id': 'incomplete'}])):
            self.path.write_text(json.dumps(invalid), encoding='utf-8')
            with self.assertRaises(ValueError):
                view.load_sessions(self.root, self.path)

    def test_dashboard_sessions_only_is_explicit_snapshot_and_preserves_input(self):
        before = self.path.read_bytes()
        dash = REPO / 'plugin/hglos/skills/hglos-dashboard/scripts/dashboard.py'
        result = subprocess.run([sys.executable, str(dash), '--repo', str(self.root),
                                 '--sessions', str(self.path), '--output', 'sessions.html'],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        html = (self.root / 'sessions.html').read_text(encoding='utf-8')
        self.assertIn('세션 협업 현황', html)
        self.assertNotIn('<script>', html)


if __name__ == '__main__':
    unittest.main()
