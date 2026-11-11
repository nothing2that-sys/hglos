"""Regression tests for verification findings Q1-Q3 (validation/fix-20261023/verify-20261023-9).

JH 2026-10-23: "Q1~Q3은 report-I9.md 3절 선택지 1(설치 잠금 + Q3 보고 수정)로 고치고 수정분에 대해 새 독립 검증 run을
돌려 주세요." Q1/Q2: installers on the same skills folder are serialised by an exclusive lock file.
Q3: a new copy left in place because no folder identity was available is reported as incomplete.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import install_skills
import package_skill

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}


def older(dest):
    install_skills.install(dest)
    for skill in package_skill.SKILLS:
        with (dest / skill / 'SKILL.md').open('ab') as stream:
            stream.write(b'\n<!-- older release -->\n')


class Q1Q2InstallLock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.dest = self.base / 'skills'

    def test_lock_path_is_beside_the_skills_folder(self):
        lock = install_skills.lock_path(self.dest)
        self.assertEqual(lock.parent, self.dest.parent)
        self.assertNotEqual(lock.parent, self.dest)

    def test_held_lock_refuses_upgrade_and_install_without_changes(self):
        older(self.dest)
        lock = install_skills.lock_path(self.dest)
        lock.write_text('pid 1 (another installer)\n', encoding='utf-8')
        before = tree(self.base)
        with self.assertRaises(ValueError) as caught:
            install_skills.upgrade_many([self.dest])
        self.assertIn('in progress', str(caught.exception))
        self.assertEqual(tree(self.base), before, 'a refused run changes nothing (the lock stays)')
        fresh = self.base / 'fresh' / 'skills'
        install_skills.lock_path(fresh).parent.mkdir(parents=True)
        install_skills.lock_path(fresh).write_text('held\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            install_skills.install_many([fresh])
        self.assertFalse(fresh.exists())

    def test_cli_reports_held_lock_with_exit_2(self):
        project = self.base / 'project'
        project.mkdir()
        lock = install_skills.lock_path(project / '.claude/skills')
        lock.parent.mkdir(parents=True)
        lock.write_text('held\n', encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(REPO / 'tools/install_skills.py'), '--target', 'claude',
                                 '--project', str(project), '--upgrade'],
                                capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(lock), result.stderr)

    def test_lock_released_after_success_failure_and_interrupt(self):
        older(self.dest)
        lock = install_skills.lock_path(self.dest)
        install_skills.upgrade_many([self.dest])
        self.assertFalse(lock.exists(), 'released after success')
        older_again = self.dest / 'hglos-design' / 'SKILL.md'
        older_again.write_bytes(older_again.read_bytes() + b'\n<!-- again -->\n')
        real = os.rename
        for error in (OSError('injected'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def fail(src, dst, error=error):
                    if Path(src).name.startswith('.hglos-install-'):
                        raise error
                    return real(src, dst)
                with patch.object(install_skills.os, 'rename', side_effect=fail):
                    with self.assertRaises((ValueError, KeyboardInterrupt)):
                        install_skills.upgrade_many([self.dest])
                self.assertFalse(lock.exists(), 'released after a failed or interrupted run')

    def test_second_root_lock_releases_first_root_lock(self):
        first, second = self.base / 'a' / 'skills', self.base / 'b' / 'skills'
        for dest in (first, second):
            older(dest)
        held = install_skills.lock_path(second)
        held.write_text('held\n', encoding='utf-8')
        before = tree(first)
        with self.assertRaises(ValueError):
            install_skills.upgrade_many([first, second])
        self.assertEqual(tree(first), before)
        self.assertFalse(install_skills.lock_path(first).exists())
        self.assertTrue(held.exists(), "another installer's lock is never removed")

    def test_dry_run_takes_no_lock(self):
        older(self.dest)
        held = install_skills.lock_path(self.dest)
        held.write_text('held\n', encoding='utf-8')
        self.assertTrue(install_skills.upgrade_many([self.dest], dry_run=True)[0] is not None)
        self.assertEqual(held.read_text(encoding='utf-8'), 'held\n')


class Q3NoIdentityIsReported(unittest.TestCase):
    def test_new_copy_left_without_identity_is_restore_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            older(dest)
            for path in sorted((dest / 'hglos-design').rglob('*'), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            (dest / 'hglos-design').rmdir()
            real = os.rename

            def fail_third(src, dst):
                if Path(src).name.startswith('.hglos-install-') and Path(dst).name == 'hglos-verify':
                    raise OSError('injected')
                return real(src, dst)

            with patch.object(install_skills, 'identity', return_value=None), \
                    patch.object(install_skills.os, 'rename', side_effect=fail_third):
                with self.assertRaises(ValueError) as caught:
                    install_skills.upgrade_many([dest])
            message = str(caught.exception)
            self.assertIn('restore incomplete', message)
            self.assertIn('hglos-design', message)
            self.assertNotIn('every skill was restored', message)


if __name__ == '__main__':
    unittest.main()
