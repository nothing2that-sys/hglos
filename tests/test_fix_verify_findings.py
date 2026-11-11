"""Regression tests for independent-verification findings F1-F9 (validation/fix-20261023/verify-20261023-1).

JH 2026-10-23: "F1~F9를 모두 고치고(F4·F8은 같은 결함인지 확인), 수정분에 대해 새 독립 검증 run을 돌려 주세요."
F4 and F8 are one defect (Antigravity-only setup records no policy hashes), tested once.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
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
import setup_coordination as setup
import install_skills
import package_skill
from test_fix_coordination import HookCase, control, denied

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


def load_plan_hash():
    spec = importlib.util.spec_from_file_location('plan_hash', SCRIPTS / 'plan_hash.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class F1SharedSessionStop(HookCase):
    """F1: one agent's end-of-turn must not release another agent's calls under the same session_id."""

    def two_agents_running_shell(self):
        self.start('A')
        self.start('B')
        output, main_call = self.pre('A', 'Bash', {'command': 'python build.py'})
        self.assertFalse(denied(output), output)
        output = self.send('A', 'PreToolUse', tool_name='Bash', tool_input={'command': 'git status'},
                           tool_use_id='sub-call', agent_id='sub-1', agent_type='general-purpose')
        self.assertFalse(denied(output), output)
        return main_call

    def assert_parent_call_survives(self, main_call):
        self.assertEqual([o['id'] for o in self.row('A')['operations']], [main_call])
        self.assertTrue(denied(self.edit('B', 'B.cs')[0]), "the parent's shell still holds the workspace")

    def test_subagent_stop_settles_only_that_agent(self):
        main_call = self.two_agents_running_shell()
        output = self.send('A', 'SubagentStop', agent_id='sub-1', agent_type='general-purpose', stop_hook_active=False)
        self.assertNotIn('decision', output)
        self.assert_parent_call_survives(main_call)

    def test_stop_carrying_an_agent_id_settles_only_that_agent(self):
        main_call = self.two_agents_running_shell()
        self.send('A', 'Stop', agent_id='sub-1', stop_hook_active=False)
        self.assert_parent_call_survives(main_call)

    def test_main_stop_keeps_running_subagent_call(self):
        self.two_agents_running_shell()
        self.send('A', 'Stop', stop_hook_active=False)
        self.assertEqual([o['id'] for o in self.row('A')['operations']], ['sub-call'])

    def test_claude_hooks_include_subagent_stop(self):
        self.assertIn('SubagentStop', setup.hooks_for(self.root, 'claude', Path(sys.executable)))


class F2F3PlanHashMarkdown(unittest.TestCase):
    """F2: indented ATX headings end the section. F3: fence closing honours the opening length."""

    def setUp(self):
        self.ph = load_plan_hash()

    def test_indented_heading_after_current_state_is_approved_content(self):
        plan = '# P\n## Current State\n- 현재 위치: 구현\n   ## 종결 조건\n| 1 | 시험 통과 |\n'
        changed = plan.replace('시험 통과', '시험 일부 통과')
        self.assertNotEqual(self.ph.digest(plan), self.ph.digest(changed))

    def test_longer_fence_is_not_closed_by_shorter_marker(self):
        plan = ('# P\n````md\n```\n## Current State\n- 예시\n```\n````\n본문은 승인 대상이다.\n'
                '## Current State\n- 현재 위치: 구현\n')
        value = self.ph.digest(plan)
        self.assertNotEqual(value, self.ph.digest(plan.replace('승인 대상이다', '바뀐 본문이다')))
        self.assertEqual(value, self.ph.digest(plan.replace('- 현재 위치: 구현', '- 현재 위치: 검증')))

    def test_closing_fence_needs_only_whitespace_after_it(self):
        plan = ('# P\n```\n``` not a close\n## Current State\n- 예시\n```\n본문.\n'
                '## Current State\n- 현재 위치: 구현\n')
        self.assertNotEqual(self.ph.digest(plan), self.ph.digest(plan.replace('본문.', '바뀐 본문.')))


class F4F8AntigravityPolicyManifest(unittest.TestCase):
    """F4 = F8: policy hashes are recorded even when no hook adapter is installed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.manifest = self.root / '.hglos/bin/manifest.json'

    def setup(self, host, *extra):
        return subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(self.root),
                               '--host', host, '--mode', 'scoped', *extra],
                              capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)

    def test_antigravity_records_policy_hashes_and_later_replaces_managed_copy(self):
        self.assertEqual(self.setup('antigravity', '--apply').returncode, 0)
        record = json.loads(self.manifest.read_text(encoding='utf-8'))
        policy = self.root / '.hglos/COORDINATION.md'
        self.assertEqual(record['COORDINATION.md'], hashlib.sha256(policy.read_bytes()).hexdigest())
        self.assertFalse((self.root / '.hglos/bin/coordination_hook.py').exists(), 'no hook adapter for antigravity')
        current = policy.read_bytes()
        older = b'# policy from an earlier managed apply\n'
        policy.write_bytes(older)
        record['COORDINATION.md'] = hashlib.sha256(older).hexdigest()
        self.manifest.write_text(json.dumps(record), encoding='utf-8')
        result = self.setup('antigravity', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(policy.read_bytes(), current)

    def test_antigravity_apply_keeps_recorded_hook_copy_hashes(self):
        self.assertEqual(self.setup('claude', '--apply').returncode, 0)
        before = json.loads(self.manifest.read_text(encoding='utf-8'))
        self.assertEqual(self.setup('antigravity', '--apply').returncode, 0)
        after = json.loads(self.manifest.read_text(encoding='utf-8'))
        for name in ('coordination.py', 'coordination_hook.py'):
            self.assertEqual(after.get(name), before[name])


class F5ControlPathFromPinnedHook(unittest.TestCase):
    """F5: the pinned hook accepts the documented skill-path control CLI when it is the same code."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(self.root),
                                 '--host', 'claude', '--mode', 'scoped', '--apply'],
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.pinned = self.root / '.hglos/bin/coordination_hook.py'
        self.payload = {'session_id': 'pinned', 'cwd': str(self.root)}
        self.invoke('SessionStart')
        self.sid = hook.participant('claude', self.payload)
        with c.Registry(self.root) as reg:
            reg.register('codex', sid='peer')
            reg.claim('peer', [('file', 'A.cs')], 1)

    def invoke(self, event, **fields):
        result = subprocess.run([sys.executable, '-B', str(self.pinned), '--repo', str(self.root), '--host', 'claude',
                                 '--event', event], input=json.dumps(dict(self.payload, hook_event_name=event, **fields)),
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def shell(self, script, tid):
        command = setup.command([sys.executable, script, '--repo', self.root, 'status'], os.name == 'nt')
        return self.invoke('PreToolUse', tool_name='Bash', tool_input={'command': command}, tool_use_id=tid)

    def test_pinned_control_path_is_a_control_call(self):
        # Changed by verify-20261023-2 G1 (JH 2026-10-23): an identical copy elsewhere is no longer trusted
        # (its folder's modules would load first); the documented path is the pinned copy.
        self.assertFalse(denied(self.shell(self.root / '.hglos/bin/coordination.py', 't1')))
        with c.Registry(self.root) as reg:
            self.assertEqual(reg.snapshot()['sessions'][0]['operations'], [])

    def test_modified_copy_is_not_a_control_call(self):
        other = self.root.parent / (self.root.name + '-other')
        other.mkdir()
        self.addCleanup(shutil.rmtree, other, True)
        (other / 'coordination.py').write_bytes((SCRIPTS / 'coordination.py').read_bytes() + b'\n# altered\n')
        self.assertTrue(denied(self.shell(other / 'coordination.py', 't2')))

    def test_coordinate_skill_documents_the_pinned_control_path(self):
        text = (REPO / 'plugin/hglos/skills/hglos-coordinate/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('.hglos/bin/coordination.py', text)


class F6ImplementPlanHashCommand(unittest.TestCase):
    """F6: the documented plan_hash command path exists in both the plugin and the installed layout."""

    def test_plan_hash_command_resolves_in_both_layouts(self):
        skill = 'hglos-implement'
        skill_dir = (REPO / 'plugin/hglos/skills' / skill).resolve()
        text = (skill_dir / 'SKILL.md').read_text(encoding='utf-8')
        paths = re.findall(r'<이 스킬>/(\S*plan_hash\.py)', text)
        self.assertTrue(paths, 'implement SKILL.md must give a runnable plan_hash.py command')
        arcs = {arc for arc, _ in package_skill.entries(skill)}
        self.assertTrue(any((skill_dir / p).resolve().is_file() for p in paths), f'plugin layout: {paths}')
        self.assertTrue(any(posixpath.normpath(f'{skill}/{p}') in arcs for p in paths), f'installed layout: {paths}')


class F7RestoreFailureIsReported(unittest.TestCase):
    """F7: if both the swap and the immediate restore fail, the result says restore is incomplete."""

    def test_failed_restore_of_current_skill_is_reported_and_backup_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            install_skills.install(dest)
            target = dest / 'hglos-design'
            (target / 'SKILL.md').write_bytes((target / 'SKILL.md').read_bytes() + b'\n<!-- old -->\n')
            old = (target / 'SKILL.md').read_bytes()
            real = os.rename

            def flaky(src, dst):
                src, dst = Path(src), Path(dst)
                if src.name.startswith('.hglos-install-') or (dst == target and 'hglos-skill-backups' in src.parts):
                    raise OSError('injected rename failure')
                return real(src, dst)

            with patch.object(install_skills.os, 'rename', side_effect=flaky):
                with self.assertRaises(ValueError) as caught:
                    install_skills.upgrade_many([dest])
            message = str(caught.exception)
            self.assertIn('restore incomplete', message)
            self.assertNotIn('every skill was restored', message)
            backups = list((dest.parent / 'hglos-skill-backups').rglob('hglos-design/SKILL.md'))
            self.assertEqual([p.read_bytes() for p in backups], [old], 'the only copy of the old skill must be kept')


class F9UserHooksMentioningTheName(unittest.TestCase):
    """F9: only managed HGLOS handlers for this project are replaced; look-alike user hooks are kept."""

    def test_lookalike_user_hooks_survive_and_old_hglos_hook_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            settings = root / '.claude/settings.local.json'
            settings.parent.mkdir()
            users = [{'type': 'command', 'command': 'echo checking coordination_hook.py'},
                     {'type': 'command', 'command': 'python C:/other-tool/coordination_hook.py --verbose'}]
            old = {'type': 'command', 'command': f'python /old/hglos-coordinate/scripts/coordination_hook.py '
                                                 f'--repo {root.as_posix()} --host claude --event Stop'}
            settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': users}, {'hooks': [old]}]}}), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(root),
                                     '--host', 'claude', '--apply'],
                                    capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            stop = json.loads(settings.read_text(encoding='utf-8'))['hooks']['Stop']
            handlers = [h for group in stop for h in group['hooks']]
            for user in users:
                self.assertIn(user, handlers)
            self.assertNotIn(old, handlers)
            self.assertEqual(sum('.hglos' in h['command'] for h in handlers), 1)


if __name__ == '__main__':
    unittest.main()
