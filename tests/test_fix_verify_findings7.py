"""Regression tests for verification finding P1 (validation/fix-20261023/verify-20261023-8).

JH 2026-10-23: "P1은 report-I8.md 3절 선택지 1(stage 디렉터리 식별자 대조)로 고치고 수정분에 대해 새 독립 검증 run을
돌려 주세요." Restore deletes something at a target only when it is the very staging folder we renamed there.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import install_skills
import package_skill


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}


class P1ForeignFolderIsNeverDeleted(unittest.TestCase):
    def run_case(self, skill, remove):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            install_skills.install(dest)
            for name in package_skill.SKILLS:
                with (dest / name / 'SKILL.md').open('ab') as stream:
                    stream.write(b'\n<!-- older release -->\n')
            if remove:
                for path in sorted((dest / skill).rglob('*'), reverse=True):
                    path.unlink() if path.is_file() else path.rmdir()
                (dest / skill).rmdir()
            old = tree(dest / skill) if not remove else None
            real = os.rename

            def concurrent_creation(src, dst):
                if Path(src).name.startswith('.hglos-install-') and Path(dst).name == skill:
                    # Another installer creates the same skill folder just as our swap fails.
                    Path(dst).mkdir()
                    (Path(dst) / 'foreign.txt').write_bytes(b'created by another process\n')
                    raise OSError('injected concurrent creation')
                return real(src, dst)

            with patch.object(install_skills.os, 'rename', side_effect=concurrent_creation):
                with self.assertRaises(ValueError) as caught:
                    install_skills.upgrade_many([dest])
            self.assertEqual((dest / skill / 'foreign.txt').read_bytes(), b'created by another process\n',
                             "another process's folder must never be deleted by our rollback")
            if not remove:
                self.assertIn('restore incomplete', str(caught.exception))
                backups = list((dest.parent / 'hglos-skill-backups').glob(f'*/{skill}'))
                self.assertEqual([tree(b) for b in backups], [old], 'the old copy must be kept as a backup')
            for name in package_skill.SKILLS:
                if name != skill and (dest / name).exists():
                    self.assertNotIn('foreign.txt', tree(dest / name))

    def test_existing_skill_swap_failure_keeps_foreign_folder_and_backup(self):
        self.run_case('hglos-verify', remove=False)

    def test_new_skill_swap_failure_keeps_foreign_folder(self):
        self.run_case('hglos-coordinate', remove=True)

    def test_identity_matches_only_the_renamed_staging_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / 'stage'
            stage.mkdir()
            ident = install_skills.identity(stage)
            os.rename(stage, base / 'target')
            self.assertEqual(install_skills.identity(base / 'target'), ident)
            (base / 'other').mkdir()
            self.assertNotEqual(install_skills.identity(base / 'other'), ident)


if __name__ == '__main__':
    unittest.main()
