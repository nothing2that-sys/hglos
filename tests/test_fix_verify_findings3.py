"""Regression tests for third-verification findings H1-H2 (validation/fix-20261023/verify-20261023-3).

JH 2026-10-23: "verify-20261023-3/verdict.md의 H1·H2를 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/scripts'
ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


class H1AnyInterpreterName(unittest.TestCase):
    """H1: a hook generated with a non-`python*` interpreter (e.g. PyPy) is still recognised as managed."""

    def test_reapply_with_pypy_named_interpreter_is_identical(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root = base / 'project'
            root.mkdir()
            interpreter = base / ('pypy3.exe' if os.name == 'nt' else 'pypy3')
            interpreter.write_bytes(b'')  # setup only requires an existing file; it never runs it

            def apply():
                result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(root),
                                         '--host', 'both', '--mode', 'scoped', '--python', str(interpreter), '--apply'],
                                        capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)

            def snapshot():
                return {p: p.read_bytes() for p in root.rglob('*') if p.is_file() and 'runtime' not in p.parts}

            apply()
            before = snapshot()
            apply()
            self.assertEqual(snapshot(), before, 're-apply must not add another hook')
            for name in ('.claude/settings.local.json', '.codex/hooks.json'):
                hooks = json.loads((root / name).read_text(encoding='utf-8'))['hooks']
                for event, groups in hooks.items():
                    count = sum('coordination_hook.py' in h['command'] for g in groups for h in g['hooks'])
                    self.assertEqual(count, 1, f'{name} {event}')


class H2NestedCurrentStateIsCounted(unittest.TestCase):
    """H2: every Current State heading outside fences counts, including one nested inside the section."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('plan_hash', SCRIPTS / 'plan_hash.py')
        self.ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ph)

    def test_nested_duplicate_is_an_error(self):
        plan = '# P\n## Current State\n- 현재 위치: 구현\n### Current State\n- 다른 기록\n'
        with self.assertRaises(ValueError):
            self.ph.digest(plan)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'plan.md'
            path.write_text(plan, encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'plan_hash.py'), str(path)],
                                    capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=30)
            self.assertEqual(result.returncode, 2)

    def test_fenced_example_inside_the_section_is_not_counted(self):
        plan = '# P\n## Current State\n- 현재 위치: 구현\n```md\n### Current State\n```\n## 부록\n본문\n'
        self.assertNotEqual(self.ph.digest(plan), self.ph.digest(plan.replace('본문', '바뀐 본문')))


if __name__ == '__main__':
    unittest.main()
