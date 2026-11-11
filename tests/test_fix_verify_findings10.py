"""Regression tests for full-scope verification findings T1-T3 (validation/fix-20261023/verify-20261023-12).

JH 2026-10-23: "verify-20261023-12/verdict.md의 T1~T3을 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / 'tools'))
import coordination as c
import coordination_hook as hook
import install_skills
import package_skill
from test_fix_plan_gate import section

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
EXTERNAL = '기대 해시는 계획서 밖에서 가져온다'


class T1ExpectedHashComesFromOutsideThePlan(unittest.TestCase):
    """T1: the --expect value is the hash the approver confirmed, not a line inside the checked plan."""

    def test_design_and_implement_state_the_external_source(self):
        for skill in ('hglos-design', 'hglos-implement'):
            with self.subTest(skill=skill):
                text = (REPO / 'plugin/hglos/skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
                self.assertIn(EXTERNAL, text)

    def test_implement_no_longer_reads_the_expected_hash_from_current_state(self):
        text = (REPO / 'plugin/hglos/skills/hglos-implement/SKILL.md').read_text(encoding='utf-8')
        start = section(text, '시작 조건')
        self.assertIn(EXTERNAL, start)
        self.assertNotIn('--expect <Current State 의 승인 시점 해시>', start)

    def test_template_marks_the_plan_line_informational(self):
        text = (REPO / 'plugin/hglos/skills/hglos-design/assets/slice-plan-template.md').read_text(encoding='utf-8')
        line = next(l for l in text.splitlines() if '승인 시점 해시' in l)
        self.assertIn('참고용', line)


class T2RunnableControlCommands(unittest.TestCase):
    """T2: every `$ ` command line in the hook context is a standalone control call, as documented."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        c.initialize(self.root, 'scoped')
        self.payload = {'session_id': 't2', 'cwd': str(self.root), 'hook_event_name': 'SessionStart'}
        self.sid = hook.participant('claude', self.payload)

    def context(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'coordination_hook.py'), '--repo', str(self.root),
                                 '--host', 'claude', '--event', 'SessionStart'], input=json.dumps(self.payload),
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext']

    def test_command_lines_are_accepted_control_calls(self):
        # Long paths (e.g. an extracted public ZIP) may leave room for only some commands within 1,000
        # characters; whatever is shown must be whole and runnable, and the recovery command (release) is kept.
        text = self.context()
        self.assertLessEqual(len(text), 1000)
        commands = [line[2:] for line in text.splitlines() if line.startswith('$ ')]
        self.assertTrue(commands, text)
        actions = set()
        for command in commands:
            with self.subTest(command=command[-60:]):
                self.assertNotIn('|', command)
                self.assertTrue(hook.control_call(command, self.root, self.sid), command)
                actions.add(next(a for a in ('status', 'release') if re.search(rf"\b{a}\b", command)))
        self.assertIn('release', actions)

    def test_very_long_paths_keep_commands_whole(self):
        import coordination_hook
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # About 200 characters: both commands no longer fit in 1,000, while .hglos/runtime/... stays under
        # Windows MAX_PATH (260) whatever the temp folder length is.
        base = Path(temp.name).resolve()
        long_root = base / ('deep-' + 'x' * max(10, 200 - len(str(base)) - 6))
        long_root.mkdir()
        c.initialize(long_root, 'scoped')
        payload = dict(self.payload, cwd=str(long_root))
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'coordination_hook.py'), '--repo', str(long_root),
                                 '--host', 'claude', '--event', 'SessionStart'], input=json.dumps(payload),
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(text), 1000)
        for line in text.splitlines():
            if line.startswith('$ '):
                with self.subTest(line=line[-60:]):
                    self.assertTrue(coordination_hook.control_call(line[2:], long_root, self.sid), line)

    def test_coordinate_skill_documents_the_dollar_lines(self):
        text = (REPO / 'plugin/hglos/skills/hglos-coordinate/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('`$ `', text)


class T3OccupiedNewSkillIsReported(unittest.TestCase):
    """T3: a previously missing skill whose path is now taken by a folder we did not create is incomplete."""

    def test_foreign_folder_at_new_skill_path_reports_restore_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            install_skills.install(dest)
            for path in sorted((dest / 'hglos-coordinate').rglob('*'), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            (dest / 'hglos-coordinate').rmdir()
            with (dest / 'hglos-design/SKILL.md').open('ab') as stream:
                stream.write(b'\n<!-- older -->\n')
            real = os.rename

            def foreign(src, dst):
                if Path(src).name.startswith('.hglos-install-') and Path(dst).name == 'hglos-coordinate':
                    Path(dst).mkdir()
                    (Path(dst) / 'foreign.txt').write_bytes(b'other process\n')
                    raise OSError('injected concurrent creation')
                return real(src, dst)

            with patch.object(install_skills.os, 'rename', side_effect=foreign):
                with self.assertRaises(ValueError) as caught:
                    install_skills.upgrade_many([dest])
            message = str(caught.exception)
            self.assertIn('restore incomplete', message)
            self.assertIn('hglos-coordinate', message)
            self.assertEqual((dest / 'hglos-coordinate/foreign.txt').read_bytes(), b'other process\n')


if __name__ == '__main__':
    unittest.main()
