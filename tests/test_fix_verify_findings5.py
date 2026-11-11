"""Regression tests for full-scope verification findings M1-M3 (validation/fix-20261023/verify-20261023-6).

JH 2026-10-23: "verify-20261023-6/verdict.md의 M1~M3을 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
"""

import hashlib
import json
import os
from pathlib import Path
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
import install_skills
import package_skill
import setup_coordination as setup
from test_fix_coordination import find_bash

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}


class M1InterruptStillRestores(unittest.TestCase):
    """M1: Ctrl+C (KeyboardInterrupt) mid-upgrade restores every skill and is re-raised."""

    def test_keyboard_interrupt_restores_and_propagates(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            install_skills.install(dest)
            for skill in package_skill.SKILLS:
                with (dest / skill / 'SKILL.md').open('ab') as stream:
                    stream.write(b'\n<!-- older release -->\n')
            old = {skill: tree(dest / skill) for skill in package_skill.SKILLS}
            real, calls = os.rename, []

            def interrupt_third_swap(src, dst):
                if Path(src).name.startswith('.hglos-install-'):
                    calls.append(src)
                    if len(calls) == 3:
                        raise KeyboardInterrupt
                return real(src, dst)

            with patch.object(install_skills.os, 'rename', side_effect=interrupt_third_swap):
                with self.assertRaises(KeyboardInterrupt):
                    install_skills.upgrade_many([dest])
            self.assertEqual({skill: tree(dest / skill) for skill in package_skill.SKILLS}, old)
            self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(package_skill.SKILLS))


class M2GuardChecksBothPinnedFiles(unittest.TestCase):
    """M2: if only the pinned coordination.py is missing, PreToolUse blocks and other events exit 0."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'setup_coordination.py'), '--repo', str(self.root),
                                 '--host', 'both', '--mode', 'scoped', '--apply'],
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def runs(self):
        for name in ('.claude/settings.local.json', '.codex/hooks.json'):
            for event, groups in json.loads((self.root / name).read_text(encoding='utf-8'))['hooks'].items():
                handler = groups[0]['hooks'][0]
                if name.startswith('.codex'):
                    shells = ['bash'] + (['windows'] if os.name == 'nt' else [])
                else:
                    shells = ['powershell' if handler.get('shell') == 'powershell' else 'bash']
                for shell in shells:
                    yield name, event, handler, shell

    def execute(self, handler, shell, event):
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
        payload = {'session_id': 'm2', 'cwd': str(self.root), 'hook_event_name': event, 'tool_use_id': 't',
                   'tool_name': 'Edit', 'tool_input': {'file_path': 'A.cs'}}
        return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=ENV, timeout=60)

    def test_missing_coordinator_blocks_only_pre_tool_use(self):
        (self.root / '.hglos/bin/coordination.py').unlink()
        for name, event, handler, shell in self.runs():
            with self.subTest(file=name, event=event, shell=shell):
                result = self.execute(handler, shell, event)
                if event == 'PreToolUse':
                    self.assertTrue(result.returncode != 0 or '"deny"' in result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr[-500:])
                    self.assertNotIn('Traceback', result.stderr)

    def test_generated_commands_stay_recognised(self):
        for host in ('claude', 'codex'):
            for event, groups in setup.hooks_for(self.root, host, Path(sys.executable)).items():
                with self.subTest(host=host, event=event):
                    self.assertTrue(setup.ours(groups[0]['hooks'][0], self.root))


class M3AtomicPinnedCopyWrite(unittest.TestCase):
    """M3: a failed write leaves the previous managed copy intact, so a plain re-run recovers."""

    def test_failed_replace_keeps_old_copy_and_rerun_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            argv = ['setup_coordination.py', '--repo', str(root), '--host', 'claude', '--mode', 'scoped', '--apply']
            with patch.object(sys, 'argv', argv):
                self.assertEqual(setup.main(), 0)
            pinned = root / '.hglos/bin/coordination_hook.py'
            manifest = root / '.hglos/bin/manifest.json'
            older = b'# managed hook copy from an earlier --apply\n'
            pinned.write_bytes(older)
            record = json.loads(manifest.read_text(encoding='utf-8'))
            record['coordination_hook.py'] = hashlib.sha256(older).hexdigest()
            manifest.write_text(json.dumps(record), encoding='utf-8')
            real = os.replace

            def fail_for_pinned(src, dst):
                if Path(dst).name == 'coordination_hook.py':
                    raise OSError('injected replace failure')
                return real(src, dst)

            with patch.object(sys, 'argv', argv), patch.object(setup.os, 'replace', side_effect=fail_for_pinned):
                self.assertEqual(setup.main(), 2)
            self.assertEqual(pinned.read_bytes(), older, 'the managed copy must not be truncated')
            self.assertEqual(sorted(p.name for p in pinned.parent.iterdir()),
                             ['coordination.py', 'coordination_hook.py', 'manifest.json'], 'no temporary files left')
            with patch.object(sys, 'argv', argv):
                self.assertEqual(setup.main(), 0)
            self.assertEqual(pinned.read_bytes(), (SCRIPTS / 'coordination_hook.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
