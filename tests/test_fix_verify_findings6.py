"""Regression tests for verification findings N1-N2 (validation/fix-20261023/verify-20261023-7).

JH 2026-10-23: "verify-20261023-7/verdict.md의 N1·N2를 고치고 수정분에 대해 새 독립 검증 run을 돌려 주세요."
Ctrl+C can land right AFTER a rename completed but before bookkeeping; restore must follow the real paths.
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


class InterruptAfterRename(unittest.TestCase):
    def run_case(self, interrupt_on, remove=None):
        """Upgrade an older install; raise KeyboardInterrupt right after the chosen rename really happened."""
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp).resolve() / 'skills'
            install_skills.install(dest)
            for skill in package_skill.SKILLS:
                with (dest / skill / 'SKILL.md').open('ab') as stream:
                    stream.write(b'\n<!-- older release -->\n')
            if remove:
                for path in sorted((dest / remove).rglob('*'), reverse=True):
                    path.unlink() if path.is_file() else path.rmdir()
                (dest / remove).rmdir()
            old = {skill: tree(dest / skill) if (dest / skill).exists() else None for skill in package_skill.SKILLS}
            real = os.rename

            def rename_then_interrupt(src, dst):
                real(src, dst)
                if interrupt_on(Path(src), Path(dst)):
                    raise KeyboardInterrupt

            with patch.object(install_skills.os, 'rename', side_effect=rename_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    install_skills.upgrade_many([dest])
            now = {skill: tree(dest / skill) if (dest / skill).exists() else None for skill in package_skill.SKILLS}
            self.assertEqual(now, old, 'every skill must be back to its pre-upgrade bytes')
            leftovers = sorted(p.name for p in dest.iterdir() if p.name not in package_skill.SKILLS)
            self.assertEqual(leftovers, [], 'no staging folder may remain')

    def test_n2_interrupt_right_after_backup_move(self):
        self.run_case(lambda src, dst: src.name == 'hglos-verify' and 'hglos-skill-backups' in dst.parts)

    def test_n1_interrupt_right_after_new_copy_swapped_in(self):
        self.run_case(lambda src, dst: src.name.startswith('.hglos-install-') and dst.name == 'hglos-verify')

    def test_interrupt_right_after_installing_a_previously_missing_skill(self):
        self.run_case(lambda src, dst: src.name.startswith('.hglos-install-') and dst.name == 'hglos-coordinate',
                      remove='hglos-coordinate')


if __name__ == '__main__':
    unittest.main()
