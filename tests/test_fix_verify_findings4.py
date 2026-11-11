"""Regression tests for fourth-verification finding K1 (validation/fix-20261023/verify-20261023-4).

JH 2026-10-23: "K1은 report-I4.md 3절 선택지 1(관리 표식 + 구버전은 python 계열 이름 제한)로 고치고 수정분에 대해
새 독립 검증 run을 돌려 주세요."
Managed handlers carry a marker; unmarked (pre-marker) handlers are HGLOS only when run by a python-named program.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/scripts'
sys.path.insert(0, str(SCRIPTS))
import setup_coordination as setup

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


class K1ManagedMarker(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'project'
        (self.root / '.claude').mkdir(parents=True)
        self.settings = self.root / '.claude/settings.local.json'

    def apply(self, *extra):
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(self.root),
                                 '--host', 'claude', '--mode', 'scoped', '--apply', *extra],
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def stop_handlers(self):
        groups = json.loads(self.settings.read_text(encoding='utf-8'))['hooks']['Stop']
        return [h for group in groups for h in group['hooks']]

    def test_generated_handlers_carry_the_marker(self):
        for host in ('claude', 'codex'):
            for event, groups in setup.hooks_for(self.root, host, Path(sys.executable)).items():
                with self.subTest(host=host, event=event):
                    handler = groups[0]['hooks'][0]
                    self.assertTrue(setup.ours(handler, self.root))
                    self.assertIn(setup.MANAGED_MARK, handler['command'])

    def test_echo_hooks_naming_hglos_locations_are_kept(self):
        args = f'--repo {self.root.as_posix()} --host claude --event Stop'
        users = [
            {'type': 'command', 'command': f'echo {(self.root / ".hglos/bin/coordination_hook.py").as_posix()} {args}'},
            {'type': 'command', 'command': f'echo /x/hglos-coordinate/scripts/coordination_hook.py {args}'},
            {'type': 'command', 'command': f'echo /x/hglos-coordinate/scripts/coordination_hook.py {args} # {setup.MANAGED_MARK}'},
        ]
        self.settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': users}]}}), encoding='utf-8')
        self.apply()
        handlers = self.stop_handlers()
        for user in users:
            self.assertIn(user, handlers)
        self.assertEqual(sum(setup.MANAGED_MARK in h['command'] and h not in users for h in handlers), 1)

    def test_unmarked_legacy_hook_needs_a_python_named_program(self):
        args = f'--repo {self.root.as_posix()} --host claude --event Stop'
        legacy_python = {'type': 'command', 'command': f'python3 /old/hglos-coordinate/scripts/coordination_hook.py {args}'}
        legacy_other = {'type': 'command', 'command': f'pypy3 /old/hglos-coordinate/scripts/coordination_hook.py {args}'}
        self.settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [legacy_python, legacy_other]}]}}),
                                 encoding='utf-8')
        self.apply()
        handlers = self.stop_handlers()
        self.assertNotIn(legacy_python, handlers, 'pre-marker HGLOS hook run by python is replaced')
        self.assertIn(legacy_other, handlers, 'unmarked hook run by a non-python program is kept (option 1)')

    def test_marked_hook_with_non_python_interpreter_is_recognised(self):
        interpreter = self.root.parent / ('pypy3.exe' if os.name == 'nt' else 'pypy3')
        interpreter.write_bytes(b'')
        self.apply('--python', str(interpreter))
        before = self.settings.read_bytes()
        self.apply('--python', str(interpreter))
        self.assertEqual(self.settings.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
