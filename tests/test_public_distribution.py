"""License propagation and the public source archive's explicit release boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import package_skill
import package_public_source


class PublicDistribution(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_each_independent_skill_carries_canonical_license_and_notices(self):
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                items = package_skill.entries(skill)
                output = package_skill.build(skill, items, self.root)
                with zipfile.ZipFile(output) as archive:
                    for name in ('LICENSE', 'THIRD-PARTY-NOTICES.md'):
                        self.assertEqual(archive.read(f'{skill}/{name}'), (REPO / name).read_bytes())

    def test_public_source_contains_only_reviewed_entries_and_no_local_history(self):
        items = package_public_source.entries()
        archive_path = package_skill.build('public', items, self.root)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {arc for arc, _ in items})
            self.assertIn('LICENSE', archive.namelist())
            self.assertIn('plugin/hglos/LICENSE', archive.namelist())
            for name in archive.namelist():
                self.assertNotIn(name.split('/')[0], ('.git', 'validation', 'codex', 'doc', 'hglos-design'))
                # Only the explicitly reviewed, synthetic dashboard preview is a shipped image.
                if name != 'examples/dashboard/preview.png':
                    self.assertFalse(name.endswith(('.log', '.sqlite3', '.png', '.woff', '.js')))

    def test_unreviewed_validation_path_is_rejected(self):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'validation/private.log': 'README.md'}), encoding='utf-8')
        with patch.object(package_public_source, 'MANIFEST', manifest):
            with self.assertRaises(ValueError):
                package_public_source.entries()

    def test_public_source_can_install_and_rebuild_without_the_private_repository(self):
        output = package_skill.build('public', package_public_source.entries(), self.root)
        extracted = self.root / 'source'
        with zipfile.ZipFile(output) as archive:
            archive.extractall(extracted)
        project = self.root / 'project'
        project.mkdir()
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        commands = [
            [sys.executable, '-B', str(extracted / 'tools/install_skills.py'), '--target', 'codex', '--project', str(project)],
            [sys.executable, '-B', str(extracted / 'tools/package_public_source.py'), '--outdir', str(self.root / 'rebuilt')],
            [sys.executable, '-B', str(extracted / 'tools/build_demo_dashboard.py')],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=extracted, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for skill in package_skill.SKILLS:
            self.assertEqual((project / '.agents/skills' / skill / 'LICENSE').read_bytes(), (REPO / 'LICENSE').read_bytes())
        self.assertTrue((self.root / 'rebuilt/hglos-public-source.zip').is_file())
        self.assertTrue((extracted / 'examples/dashboard/index.html').is_file())

    def test_public_allowlist_includes_shared_and_fix_tests_and_plan_hash(self):
        # doc/29 condition 11 (L5).
        manifest = json.loads(package_public_source.MANIFEST.read_text(encoding='utf-8'))
        required = {'tests/test_shared_scripts.py', 'plugin/hglos/scripts/plan_hash.py'}
        required |= {p.relative_to(REPO).as_posix() for p in (REPO / 'tests').glob('test_fix_*.py')}
        self.assertEqual(sorted(required - set(manifest)), [])

    def test_extracted_public_source_passes_its_own_test_suite(self):
        # doc/29 condition 11: the whole allowlisted suite runs from a clean extracted folder.
        if os.environ.get('HGLOS_PUBLIC_SUITE_NESTED'):
            self.skipTest('already running inside the extracted public source')
        output = package_skill.build('public', package_public_source.entries(), self.root)
        extracted = self.root / 'source'
        with zipfile.ZipFile(output) as archive:
            archive.extractall(extracted)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                   HGLOS_PUBLIC_SUITE_NESTED='1')
        env.pop('HGLOS_SHARED_SCRIPTS', None)
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'], cwd=extracted,
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=1200)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])


if __name__ == '__main__':
    unittest.main()
