"""Regression tests for verification findings S1-S3 (validation/fix-20261023/verify-20261023-10).

JH 2026-10-23: "verify-20261023-10/verdict.md의 S1~S3을 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
"""

from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import install_skills
import package_skill


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}


def expected(skill):
    return {Path(arc).relative_to(skill).as_posix(): src.read_bytes() for arc, src in package_skill.entries(skill)}


class S1OneShotIterables(unittest.TestCase):
    """S1: roots given as a one-shot iterator are not consumed by the preflight."""

    def test_install_and_upgrade_accept_an_iterator(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            self.assertEqual(len(install_skills.install_many(iter([dest]))), len(package_skill.SKILLS))
            for skill in package_skill.SKILLS:
                self.assertEqual(tree(dest / skill), expected(skill))
            with (dest / 'hglos-design/SKILL.md').open('ab') as stream:
                stream.write(b'\n<!-- older -->\n')
            changed, _ = install_skills.upgrade_many(iter([dest]))
            self.assertEqual([Path(p).name for p in changed], ['hglos-design'])
            self.assertEqual(tree(dest / 'hglos-design'), expected('hglos-design'))


class S2NoChangeDecidedUnderTheLock(unittest.TestCase):
    """S2: "nothing to do" is only decided while holding the lock, so an in-flight installer is not missed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dest = Path(temp.name).resolve() / 'skills'
        install_skills.install(self.dest)
        self.lock = install_skills.lock_path(self.dest)

    def test_identical_upgrade_and_install_refuse_while_another_installer_holds_the_lock(self):
        self.lock.write_text('held\n', encoding='utf-8')
        for action in (install_skills.upgrade_many, install_skills.install_many):
            with self.subTest(action=action.__name__):
                with self.assertRaises(ValueError) as caught:
                    action([self.dest])
                self.assertIn('in progress', str(caught.exception))
        self.assertEqual(self.lock.read_text(encoding='utf-8'), 'held\n')

    def test_identical_runs_without_a_lock_change_nothing_and_leave_no_lock(self):
        before = tree(self.dest.parent)
        self.assertEqual(install_skills.upgrade_many([self.dest]), ([], []))
        self.assertEqual(install_skills.install_many([self.dest]), [])
        self.assertEqual(tree(self.dest.parent), before)
        self.assertFalse(self.lock.exists())

    def test_dry_run_still_needs_no_lock(self):
        self.lock.write_text('held\n', encoding='utf-8')
        self.assertEqual(install_skills.upgrade_many([self.dest], dry_run=True), ([], []))
        self.assertEqual(install_skills.install_many([self.dest], dry_run=True), [])


class S3ReadmesDescribeIncompleteRestore(unittest.TestCase):
    """S3: the documents must not promise a complete restore in every case."""

    def test_both_readmes_name_restore_incomplete(self):
        for name in ('README.md', 'integrations/public/README.md'):
            with self.subTest(document=name):
                text = (REPO / name).read_text(encoding='utf-8')
                self.assertIn('restore incomplete', text)


if __name__ == '__main__':
    unittest.main()
