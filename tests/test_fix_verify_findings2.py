"""Regression tests for re-verification findings G1-G4 (validation/fix-20261023/verify-20261023-2).

JH 2026-10-23: "verify-20261023-2/verdict.md의 G1~G4를 모두 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
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
from test_fix_coordination import denied

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


def setup_apply(root, host='claude'):
    return subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(root),
                           '--host', host, '--mode', 'scoped', '--apply'],
                          capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)


class G1ControlCallOnlyFromPinnedCopy(unittest.TestCase):
    """G1: a copy elsewhere would import modules from its own folder first, so only the pinned copy is trusted."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'project'
        self.root.mkdir()
        result = setup_apply(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.pinned = self.root / '.hglos/bin'
        self.payload = {'session_id': 'g1', 'cwd': str(self.root)}
        self.invoke('SessionStart')
        with c.Registry(self.root) as reg:
            reg.register('codex', sid='peer')
            reg.claim('peer', [('file', 'A.cs')], 1)

    def invoke(self, event, **fields):
        result = subprocess.run([sys.executable, '-B', str(self.pinned / 'coordination_hook.py'), '--repo', str(self.root),
                                 '--host', 'claude', '--event', event],
                                input=json.dumps(dict(self.payload, hook_event_name=event, **fields)),
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def shell(self, script, tid):
        command = setup.command([sys.executable, script, '--repo', self.root, 'status'], os.name == 'nt')
        return self.invoke('PreToolUse', tool_name='Bash', tool_input={'command': command}, tool_use_id=tid)

    def test_pinned_copy_is_a_control_call(self):
        self.assertFalse(denied(self.shell(self.pinned / 'coordination.py', 't1')))
        with c.Registry(self.root) as reg:
            self.assertEqual(reg.snapshot()['sessions'][0]['operations'], [])

    def test_identical_copy_beside_a_shadowing_module_is_a_shell_call(self):
        other = self.root.parent / 'elsewhere'
        other.mkdir()
        shutil.copyfile(SCRIPTS / 'coordination.py', other / 'coordination.py')
        (other / 'argparse.py').write_text('raise SystemExit("shadow module executed")\n', encoding='utf-8')
        self.assertTrue(denied(self.shell(other / 'coordination.py', 't2')), 'peer claim must block an untrusted shell')

    def test_skill_copy_is_a_shell_call(self):
        self.assertTrue(denied(self.shell(SCRIPTS / 'coordination.py', 't3')))


class G2G3ManagedHookIdentification(unittest.TestCase):
    """G2: parse the command, do not match substrings. G3: paths with apostrophes are recognised."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def handlers(self, root):
        data = json.loads((root / '.claude/settings.local.json').read_text(encoding='utf-8'))
        return {event: [h for group in groups for h in group['hooks']] for event, groups in data['hooks'].items()}

    def test_lookalike_user_hooks_with_hglos_arguments_are_kept(self):
        root = self.base / 'project'
        root.mkdir()
        (root / '.claude').mkdir()
        args = f'--repo {root.as_posix()} --host claude --event Stop'
        users = [{'type': 'command', 'command': f'echo coordination_hook.py {args}'},
                 {'type': 'command', 'command': f'python C:/other-tool/coordination_hook.py {args}'},
                 {'type': 'command', 'command': f'python /old/hglos-coordinate/scripts/coordination_hook.py '
                                                f'--repo {root.as_posix()}-other --host claude --event Stop'}]
        managed = [{'type': 'command', 'command': f'python /old/hglos-coordinate/scripts/coordination_hook.py {args}'},
                   {'type': 'command', 'command': f'python /src/plugin/hglos/scripts/coordination_hook.py {args}'}]
        (root / '.claude/settings.local.json').write_text(
            json.dumps({'hooks': {'Stop': [{'hooks': users}, {'hooks': managed}]}}), encoding='utf-8')
        result = setup_apply(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.handlers(root)['Stop']
        for user in users:
            self.assertIn(user, stop)
        for old in managed:
            self.assertNotIn(old, stop)
        self.assertEqual(sum('.hglos' in h['command'] for h in stop), 1)

    def test_apostrophe_in_project_path_does_not_duplicate_hooks(self):
        root = self.base / "O'Brien project"
        root.mkdir()
        for host in ('claude', 'both'):
            with self.subTest(host=host):
                self.assertEqual(setup_apply(root, host).returncode, 0)
                before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file() and 'runtime' not in p.parts}
                self.assertEqual(setup_apply(root, host).returncode, 0)
                after = {p: p.read_bytes() for p in root.rglob('*') if p.is_file() and 'runtime' not in p.parts}
                self.assertEqual(after, before, 're-apply must be byte-identical')
                for event, handlers in self.handlers(root).items():
                    self.assertEqual(sum('coordination_hook.py' in h['command'] for h in handlers), 1, event)


class G4EmptyAtxHeading(unittest.TestCase):
    """G4: a bare `##` is a heading and ends the Current State section."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('plan_hash', SCRIPTS / 'plan_hash.py')
        self.ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ph)

    def test_empty_heading_ends_current_state(self):
        for empty in ('##', '## ##', '  ##   '):
            with self.subTest(heading=empty):
                plan = f'# P\n## Current State\n- 현재 위치: 구현\n{empty}\n승인 대상 본문\n'
                self.assertNotEqual(self.ph.digest(plan), self.ph.digest(plan.replace('승인 대상 본문', '바뀐 본문')))

    def test_hash_without_space_is_not_a_heading(self):
        plan = '# P\n## Current State\n- 현재 위치: 구현\n#태그 메모\n'
        self.assertEqual(self.ph.digest(plan), self.ph.digest(plan.replace('#태그 메모', '#태그 다른 메모')))

    def test_closing_sequence_needs_preceding_space(self):
        plan = '# P\n## Current State ##\n- 현재 위치\n## C# 노트\n본문\n'
        self.assertNotEqual(self.ph.digest(plan), self.ph.digest(plan.replace('본문', '바뀐 본문')))


if __name__ == '__main__':
    unittest.main()
