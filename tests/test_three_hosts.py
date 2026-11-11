"""Install and run the same skills at each host's real project layout, without LLM calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
import install_skills as installer
import package_skill

sys.path.insert(0, str(REPO / 'plugin/hglos/scripts'))
import setup_coordination as setup
from coordination import CoordinationError


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True,
                          text=True, encoding='utf-8', timeout=30,
                          env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))


class ThreeHosts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.install_script = REPO / 'tools/install_skills.py'

    def test_all_refuses_late_host_conflict_before_any_new_install(self):
        conflict = self.root / '.agents/skills/hglos-coordinate'
        conflict.mkdir(parents=True)
        local = conflict / 'SKILL.md'
        local.write_bytes(b'local skill changes\r\n')
        result = run(self.install_script, '--target', 'all', '--project', self.root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertFalse((self.root / '.claude').exists())
        self.assertEqual(list((self.root / '.agents/skills').iterdir()), [conflict])
        self.assertEqual(local.read_bytes(), b'local skill changes\r\n')

    def test_all_rejects_file_at_late_root_or_parent_in_preview_and_apply(self):
        for relative in ('.agents/skills', '.agents'):
            for preview in (False, True):
                with self.subTest(relative=relative, preview=preview):
                    project = self.root / ('skills' if relative.endswith('skills') else 'parent') / str(preview)
                    project.mkdir(parents=True)
                    conflict = project / relative
                    conflict.parent.mkdir(parents=True, exist_ok=True)
                    conflict.write_bytes(b'preserve user file\r\n')
                    result = run(self.install_script, '--target', 'all', '--project', project,
                                 *(['--dry-run'] if preview else []))
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse((project / '.claude').exists())
                    self.assertEqual(conflict.read_bytes(), b'preserve user file\r\n')

    def test_all_dry_run_and_ambiguous_custom_location_do_not_write(self):
        result = run(self.install_script, '--target', 'all', '--project', self.root, '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])
        result = run(self.install_script, '--target', 'all', '--skills-dir', self.root / 'custom')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_project_install_deduplicates_shared_root_and_is_idempotent(self):
        roots = installer.destinations('all', project=self.root)
        self.assertEqual(set(roots), {self.root / '.claude/skills', self.root / '.agents/skills'})
        first = run(self.install_script, '--target', 'all', '--project', self.root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('New skills: 10;', first.stdout)
        for root in roots:
            for skill in package_skill.SKILLS:
                for arc, source in package_skill.entries(skill):
                    self.assertEqual((root / arc).read_bytes(), source.read_bytes())
        second = run(self.install_script, '--target', 'all', '--project', self.root)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('New skills: 0;', second.stdout)
        self.assertFalse((self.root / 'AGENTS.md').exists())
        self.assertFalse((self.root / 'GEMINI.md').exists())
        self.assertFalse((self.root / 'CLAUDE.md').exists())

    def test_all_user_paths_are_resolved_without_writing_home(self):
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(set(installer.destinations('all', user=True)), {
                self.root / '.claude/skills', self.root / '.agents/skills',
                self.root / '.gemini/config/skills', self.root / '.gemini/antigravity-cli/skills'})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_antigravity_has_no_implicit_hook_adapter(self):
        with self.assertRaises(CoordinationError):
            setup.hooks_for(self.root, 'antigravity', sys.executable)
        _, outputs = setup.install_plan(self.root, 'antigravity', sys.executable, 'scoped')
        # verify-20261023-1 F4/F8 (JH 2026-10-23 fix decision): policy hashes are recorded for every host,
        # so Antigravity also writes .hglos/bin/manifest.json - but still no hook adapter copy.
        self.assertEqual({p.relative_to(self.root).as_posix() for p in outputs},
                         {'.hglos/COORDINATION.md', '.hglos/RULES.md', 'GEMINI.md', '.hglos/bin/manifest.json'})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_setup_all_rejects_last_instruction_conflict_before_writing(self):
        gemini = self.root / 'GEMINI.md'
        gemini.write_text('User instructions\n' + setup.START + '\nlocal changes\n' + setup.END, encoding='utf-8')
        before = gemini.read_bytes()
        result = run(REPO / 'plugin/hglos/scripts/setup_coordination.py', '--repo', self.root, '--host', 'all', '--apply')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.root.iterdir()), [gemini])
        self.assertEqual(gemini.read_bytes(), before)

    def test_setup_all_installs_three_instructions_and_only_supported_hooks(self):
        script = REPO / 'plugin/hglos/scripts/setup_coordination.py'
        for name in ('CLAUDE.md', 'AGENTS.md', 'GEMINI.md'):
            (self.root / name).write_bytes(b'# Preserve existing instructions\r\n')
        first = run(script, '--repo', self.root, '--host', 'all', '--apply')
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result['manual_hosts'], ['antigravity'])
        # doc/29 M7 adds pinned hook copies under .hglos/bin/; the seven managed files stay the same.
        files = {Path(p).relative_to(self.root).as_posix() for p in result['files']}
        self.assertEqual({f for f in files if not f.startswith('.hglos/bin/')},
                         {'.hglos/COORDINATION.md', '.hglos/RULES.md', '.claude/settings.local.json',
                          '.codex/hooks.json', 'CLAUDE.md', 'AGENTS.md', 'GEMINI.md'})
        for name in ('CLAUDE.md', 'AGENTS.md', 'GEMINI.md'):
            content = (self.root / name).read_bytes()
            self.assertTrue(content.startswith(b'# Preserve existing instructions\r\n'))
            self.assertEqual(content.count(setup.START.encode()), 1)
        self.assertTrue((self.root / '.claude/settings.local.json').is_file())
        self.assertTrue((self.root / '.codex/hooks.json').is_file())
        self.assertFalse((self.root / '.gemini').exists())
        before = {p: p.read_bytes() for p in map(Path, result['files'])}
        second = run(script, '--repo', self.root, '--host', 'all', '--apply')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_installed_scripts_run_in_all_three_host_layouts(self):
        for host, location, instruction in (
                ('claude', '.claude/skills', 'CLAUDE.md'),
                ('antigravity', '.agents/skills', 'GEMINI.md'),
                ('codex', '.agents/skills', 'AGENTS.md')):
            with self.subTest(host=host):
                project = self.root / (host + ' 한글 project')
                project.mkdir()
                installed = run(self.install_script, '--target', host, '--project', project)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                skills = project / location
                for skill in package_skill.SKILLS:
                    for arc, source in package_skill.entries(skill):
                        self.assertEqual((skills / arc).read_bytes(), source.read_bytes())
                old = b'# User instructions\r\nKeep my content.\r\n'
                (project / instruction).write_bytes(old)
                scripts = skills / 'hglos-coordinate/scripts'
                applied = run(scripts / 'setup_coordination.py', '--repo', project, '--host', host,
                              '--mode', 'scoped', '--apply')
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertTrue((project / instruction).read_bytes().startswith(old))
                self.assertEqual(json.loads(applied.stdout)['manual_hosts'], ['antigravity'] if host == 'antigravity' else [])
                if host == 'antigravity':
                    self.assertFalse((project / '.claude').exists())
                    self.assertFalse((project / '.codex').exists())
                registered = run(scripts / 'coordination.py', '--repo', project, 'register', '--host', host,
                                 '--session', 'fixture-' + host, '--task', 'SYNTHETIC CLI fixture')
                self.assertEqual(registered.returncode, 0, registered.stderr)
                claimed = run(scripts / 'coordination.py', '--repo', project, 'claim', '--session', 'fixture-' + host,
                              '--version', 1, '--write', 'src/Engine.cs')
                self.assertEqual(claimed.returncode, 0, claimed.stderr)
                snapshot = run(scripts / 'coordination.py', '--repo', project, 'snapshot')
                self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
                plan = project / 'Plan.md'
                plan.write_text('# SYNTHETIC plan\n## Current State\n- 현재 위치: CLI 검사\n', encoding='utf-8')
                input_path = project / '.hglos/runtime/sessions.json'
                before = hashlib.sha256(input_path.read_bytes()).hexdigest()
                rendered = run(skills / 'hglos-dashboard/scripts/dashboard.py', '--repo', project, '--plan', plan,
                               '--sessions', input_path, '--output', 'dashboard.html')
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                html = (project / 'dashboard.html').read_text(encoding='utf-8')
                self.assertIn('fixture-' + host, html)
                self.assertIn('file:src/engine.cs', html)
                self.assertEqual(hashlib.sha256(input_path.read_bytes()).hexdigest(), before)
                rounds = project / 'rounds'
                rounds.mkdir()
                (rounds / 'round_1.json').write_text(json.dumps({'round': 1, 'codex_invoked': False, 'findings': []}), encoding='utf-8')
                failed_review = run(skills / 'hglos-verify/scripts/compare_rounds.py', rounds)
                self.assertEqual(failed_review.returncode, 1)
                self.assertIn('판정불가', failed_review.stdout)
                self.assertNotIn('codex login', failed_review.stdout)


if __name__ == '__main__':
    unittest.main()
