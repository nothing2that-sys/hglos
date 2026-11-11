"""doc/29 condition 9 (M6): install_skills.py --upgrade previews, backs up, swaps per skill, restores all on failure.

Error injection on Windows holds an open handle inside one installed skill, which makes renaming
or deleting that directory fail mid-upgrade - the same failure a running host can cause.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / 'tools/install_skills.py'
sys.path.insert(0, str(REPO / 'tools'))
import install_skills
import package_skill

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


def run(*args):
    return subprocess.run([sys.executable, '-B', str(INSTALL), *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', env=ENV, timeout=120)


def tree(folder):
    folder = Path(folder)
    if not folder.exists():
        return None
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def expected(skill):
    return {Path(arc).relative_to(skill).as_posix(): source.read_bytes() for arc, source in package_skill.entries(skill)}


def backups(stdout):
    return [Path(line.split(':', 1)[1].strip()) for line in stdout.splitlines() if line.startswith('Backup:')]


class Upgrade(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / 'project'
        self.project.mkdir()
        self.skills = self.project / '.claude/skills'

    def make_old(self, root, skills=None):
        """Install the current bundle, then turn it into a plausible older/locally edited release."""
        install_skills.install(root)
        for skill in skills or package_skill.SKILLS:
            folder = root / skill
            if skill == 'hglos-coordinate':
                for path in sorted(folder.rglob('*'), reverse=True):
                    path.unlink() if path.is_file() else path.rmdir()
                folder.rmdir()
                continue
            with (folder / 'SKILL.md').open('ab') as stream:
                stream.write(b'\n<!-- older release / local edit -->\n')
            (folder / 'old-only.txt').write_bytes(b'removed in the new release\r\n')
        return {skill: tree(root / skill) for skill in package_skill.SKILLS}

    def test_dry_run_lists_changed_skills_and_writes_nothing(self):
        self.make_old(self.skills, ['hglos-design', 'hglos-coordinate'])
        before = tree(self.project)
        result = run('--target', 'claude', '--project', self.project, '--upgrade', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('hglos-design', result.stdout)
        self.assertIn('hglos-coordinate', result.stdout)
        self.assertEqual(tree(self.project), before)
        self.assertEqual(sorted(p.name for p in self.project.parent.iterdir()), ['project'])

    def test_upgrade_backs_up_then_replaces_every_changed_skill(self):
        old = self.make_old(self.skills)
        result = run('--target', 'claude', '--project', self.project, '--upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        for skill in package_skill.SKILLS:
            self.assertEqual(tree(self.skills / skill), expected(skill), skill)
        folders = backups(result.stdout)
        self.assertEqual(len(folders), 1, result.stdout)
        for skill, content in old.items():
            if content is not None:
                self.assertEqual(tree(folders[0] / skill), content, f'backup of {skill}')
        allowed = set(package_skill.SKILLS) | {folders[0].name if folders[0].parent == self.skills else None}
        self.assertTrue({p.name for p in self.skills.iterdir()} <= allowed, list(self.skills.iterdir()))

    def test_identical_install_upgrade_is_a_noop_without_backup(self):
        install_skills.install(self.skills)
        before = tree(self.project)
        result = run('--target', 'claude', '--project', self.project, '--upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(backups(result.stdout), [])
        self.assertEqual(tree(self.project), before)

    def test_without_upgrade_flag_local_changes_are_still_refused(self):
        old = self.make_old(self.skills)
        result = run('--target', 'claude', '--project', self.project)
        self.assertEqual(result.returncode, 2)
        self.assertEqual({skill: tree(self.skills / skill) for skill in package_skill.SKILLS}, old)

    def locked_failure(self, roots, lock_root, target='claude'):
        olds = {root: self.make_old(root) for root in roots}
        names = {root: sorted(p.name for p in root.iterdir()) for root in roots}
        preview = run('--target', target, '--project', self.project, '--upgrade', '--dry-run')
        self.assertEqual(preview.returncode, 0, 'upgrade must be supported before its failure path means anything')
        handle = (lock_root / 'hglos-verify/SKILL.md').open('rb')
        try:
            result = run('--target', target, '--project', self.project, '--upgrade')
        finally:
            handle.close()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        for root in roots:
            for skill in package_skill.SKILLS:
                self.assertEqual(tree(root / skill), olds[root][skill], f'{root} / {skill} not restored')
            self.assertEqual(sorted(p.name for p in root.iterdir()), names[root], 'no stage/partial directories left')

    @unittest.skipUnless(os.name == 'nt', 'open-handle injection needs Windows sharing semantics')
    def test_mid_upgrade_failure_restores_every_skill(self):
        self.locked_failure([self.skills], self.skills)

    @unittest.skipUnless(os.name == 'nt', 'open-handle injection needs Windows sharing semantics')
    def test_failure_in_later_host_root_restores_earlier_root(self):
        agents = self.project / '.agents/skills'
        self.locked_failure([self.skills, agents], agents, target='all')


if __name__ == '__main__':
    unittest.main()
