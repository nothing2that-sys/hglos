"""doc/31 O distribution/document oracle candidate; JH must confirm before freeze.

All archives/installations stay in OS temporary folders. No shipped bundles or
global skills are written. entries() must keep returning (archive-name, Path).
Documentation semantic terms accept Korean or English, while paths/flags are exact.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import install_skills
import package_public_source
import package_skill

FILES = ('ENTRY.md', 'context_routes.json', 'context-handoff.md', 'scripts/context_index.py')


def env():
    return dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')


class ContextDistribution(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='hglos-context-distribution-')
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.assertFalse(self.tmp.is_relative_to(REPO))

    def text(self, rel):
        return (REPO / 'plugin/hglos' / rel).read_text(encoding='utf-8')

    def assert_installed_handoff_guidance(self, root, skill):
        root = root.resolve()
        form = root / 'context-handoff.md'
        self.assertTrue(form.is_file(), str(form))
        docs = [('SKILL.md', 'context-handoff.md')] if skill == 'hglos-implement' else [
            ('agents/native-validator.md', '../context-handoff.md'),
            ('agents/validator.md', '../context-handoff.md')]
        for rel, expected in docs:
            document = root / rel
            text = document.read_text(encoding='utf-8')
            # Token boundaries keep ../../context-handoff.md from satisfying the
            # installed context-handoff.md or ../context-handoff.md requirement.
            references = re.findall(r'(?<![\w./\\-])((?:\.{1,2}/)*context-handoff\.md)(?![\w./\\-])', text)
            self.assertIn(expected, references, f'{rel}: missing installed handoff guidance')
            selected = (document.parent / expected).resolve()
            self.assertTrue(selected.is_relative_to(root), str(selected))
            self.assertEqual(selected, form)
            self.assertTrue(selected.is_file())
            # Active Markdown links must also resolve to this independent skill.
            # Source-layout examples may remain as prose/code when installed
            # instructions are separately explicit; stale active links may not.
            links = re.findall(r'\[[^\]]*\]\(<?([^\s)>]*context-handoff\.md)>?\)', text)
            for reference in links:
                target = (document.parent / reference).resolve()
                self.assertTrue(target.is_relative_to(root), f'{rel}: escaping link {reference}')
                self.assertEqual(target, form, f'{rel}: wrong handoff link {reference}')
                self.assertTrue(target.is_file())

    def cli(self, root, task, *, rc=0):
        p = subprocess.run([sys.executable, '-B', str(root / 'scripts/context_index.py'),
                            'build', '--layout', 'installed', '--root', str(root), '--task', task],
                           cwd=self.tmp, env=env(), capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        self.assertEqual(p.stderr, '')
        obj = json.loads(p.stdout)
        if rc:
            self.assertEqual(set(obj), {'error'})
        else:
            self.assertEqual(obj['layout'], 'installed')
            self.assertEqual(obj['root'], str(root.resolve()))
            self.assertEqual(obj['task'], task)
            selected = {f['path']: f['sha256'] for f in obj['files']}
            for name in ('ENTRY.md', 'context_routes.json', 'RULES.md', 'HOST.md', 'COORDINATION.md'):
                self.assertEqual(selected[name], hashlib.sha256((root / name).read_bytes()).hexdigest())
            for path in selected:
                self.assertFalse(Path(path).is_absolute())
                self.assertNotIn('..', Path(path).parts)
                self.assertTrue((root / path).is_file())
                self.assertEqual(selected[path], hashlib.sha256((root / path).read_bytes()).hexdigest(), path)
            if task != 'answer':
                self.assertIn('SKILL.md', selected)
        return obj

    def files_snapshot(self, root):
        return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in root.rglob('*') if p.is_file()}

    def installed_check(self, root, task, index, repo, *, rc=0):
        path = self.tmp / 'installed-check-input.json'
        path.write_text(json.dumps(index), encoding='utf-8')
        # Harness input preparation and deliberate tampering precede this window.
        before = self.files_snapshot(self.tmp)
        try:
            result = subprocess.run([sys.executable, '-B', str(root / 'scripts/context_index.py'),
                'check', '--layout', 'installed', '--root', str(root), '--task', task,
                '--repo', str(repo), '--index', str(path)], cwd=self.tmp, env=env(),
                capture_output=True, text=True, encoding='utf-8', timeout=30)
        finally:
            self.assertEqual(self.files_snapshot(self.tmp), before, 'installed check wrote inside cwd/package/repo/index')
        self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        receipt = json.loads(result.stdout)
        if rc:
            self.assertEqual(set(receipt), {'error'})
            self.assertEqual(set(receipt['error']), {'code', 'message'})
            self.assertTrue(all(isinstance(receipt['error'][key], str) and receipt['error'][key]
                                for key in ('code', 'message')))
        else:
            self.assertIs(receipt['content_match'], True)
            self.assertEqual(receipt['files'], index['files'])
            self.assertEqual(receipt['index_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(receipt['repo'], dict(root=str(repo.resolve()), branch='installed-oracle',
                                                  head=None, unborn=True, dirty=False))
            self.assertIs(receipt['approval_verified'], False)
            self.assertEqual(receipt['model_rules_read'], 'not_attested')
            self.assertIs(receipt['effective_policy_verified'], False)
            self.assertEqual(receipt['project_instruction_coverage'], 'not_checked')
            self.assertNotEqual(receipt['baseline'], 'pinned')
        return receipt

    def test_independent_installed_check_receipt_and_tampering(self):
        repo = self.tmp / 'check-project'
        repo.mkdir()
        child_env = {k: v for k, v in env().items() if not k.upper().startswith('GIT_')}
        child_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        result = subprocess.run(['git', '--no-pager', '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'core.untrackedCache=false', '-c', f'core.hooksPath={os.devnull}', '-C', str(repo),
            'init', '-b', 'installed-oracle'], env=child_env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for skill in ('hglos-implement', 'hglos-verify'):
            for case in ('matching', 'selected-content-tampered'):
                with self.subTest(skill=skill, case=case):
                    # Each subcase independently obtains this installed root's
                    # real build output; one failed build cannot skip the other.
                    archive = package_skill.build(skill, package_skill.entries(skill), self.tmp / 'check-archives')
                    parent = self.tmp / f'{skill}-{case}'
                    with zipfile.ZipFile(archive) as source:
                        source.extractall(parent)
                    self.assertEqual(sorted(p.name for p in parent.iterdir()), [skill])
                    root = parent / skill
                    index = self.cli(root, skill.removeprefix('hglos-'))
                    if case == 'selected-content-tampered':
                        target = root / 'SKILL.md'
                        target.write_bytes(target.read_bytes() + b'\nchanged after installed build\n')
                    self.installed_check(root, skill.removeprefix('hglos-'), index, repo,
                                         rc=1 if case == 'selected-content-tampered' else 0)

    def test_each_skill_entries_contains_shared_files_with_path_contract(self):
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                items = package_skill.entries(skill)
                self.assertEqual(items, sorted(items))
                self.assertEqual(len(items), len(dict(items)))
                for arc, source in items:
                    self.assertIsInstance(source, Path)
                    self.assertTrue(source.is_file(), arc)
                mapping = dict(items)
                for name in FILES:
                    self.assertIn(f'{skill}/{name}', mapping)

    def test_independent_implement_and_verify_without_siblings(self):
        for skill in ('hglos-implement', 'hglos-verify'):
            with self.subTest(skill=skill):
                archive = package_skill.build(skill, package_skill.entries(skill), self.tmp / 'archives')
                target = self.tmp / skill
                with zipfile.ZipFile(archive) as z:
                    z.extractall(target)
                self.assertEqual(sorted(p.name for p in target.iterdir()), [skill])
                root = target / skill
                self.assertTrue((root / 'context-handoff.md').is_file())
                self.assert_installed_handoff_guidance(root, skill)
                self.cli(root, skill.removeprefix('hglos-'))
                self.cli(root, 'answer')
                self.cli(root, 'design', rc=2)
                form = (root / 'context-handoff.md').read_bytes()
                self.assertEqual(form, (REPO / 'plugin/hglos/context-handoff.md').read_bytes())

    def test_installed_handoff_guidance_resolves_without_siblings(self):
        for skill in ('hglos-implement', 'hglos-verify'):
            with self.subTest(skill=skill):
                archive = package_skill.build(skill, package_skill.entries(skill), self.tmp / 'handoff-archives')
                target = self.tmp / 'handoff-only' / skill
                with zipfile.ZipFile(archive) as z:
                    z.extractall(target)
                self.assertEqual(sorted(p.name for p in target.iterdir()), [skill])
                self.assert_installed_handoff_guidance(target / skill, skill)

    def test_installed_all_tasks_have_local_layout_and_no_sibling_fallback(self):
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                dest = self.tmp / skill / 'skills'
                with patch.object(install_skills, 'SKILLS', [skill]):
                    install_skills.install_many([dest])
                root = dest / skill
                self.assertEqual(sorted(p.name for p in dest.iterdir()), [skill])
                self.cli(root, skill.removeprefix('hglos-'))
                self.cli(root, 'answer')
                foreign = 'verify' if skill != 'hglos-verify' else 'implement'
                self.cli(root, foreign, rc=2)

    def test_source_installed_selection_correspondence(self):
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                task = skill.removeprefix('hglos-')
                dest = self.tmp / skill / 'skills'
                with patch.object(install_skills, 'SKILLS', [skill]):
                    install_skills.install_many([dest])
                installed = self.cli(dest / skill, task)
                p = subprocess.run([sys.executable, '-B', str(REPO / 'plugin/hglos/scripts/context_index.py'),
                                    'build', '--layout', 'source', '--root', str(REPO / 'plugin/hglos'), '--task', task],
                                   capture_output=True, text=True, encoding='utf-8', env=env(), timeout=30)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                source = json.loads(p.stdout)
                prefix = f'skills/{skill}/'
                expected = {f['path'][len(prefix):] if f['path'].startswith(prefix) else f['path']
                            for f in source['files']}
                self.assertEqual({f['path'] for f in installed['files']}, expected)

    def test_bundle_determinism_and_canonical_common_bytes(self):
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                items = package_skill.entries(skill)
                one = package_skill.build(skill, items, self.tmp / 'one')
                two = package_skill.build(skill, items, self.tmp / 'two')
                self.assertEqual(one.read_bytes(), two.read_bytes())
                self.assertEqual(package_skill.verify(one, items), [])
                with zipfile.ZipFile(one) as z:
                    for name in ('ENTRY.md', 'context_routes.json', 'context-handoff.md'):
                        self.assertEqual(z.read(f'{skill}/{name}'), (REPO / 'plugin/hglos' / name).read_bytes())

    def test_public_allowlist_includes_new_tool_documents_and_oracles(self):
        manifest = json.loads(package_public_source.MANIFEST.read_text(encoding='utf-8'))
        required = {f'plugin/hglos/{p}' for p in FILES}
        required |= {'tests/test_context_entry.py', 'tests/test_context_distribution.py'}
        self.assertEqual(sorted(required - set(manifest)), [])
        items = dict(package_public_source.entries())
        self.assertEqual(sorted(required - set(items)), [])
        self.assertFalse(any(p.startswith(('validation/', 'doc/')) for p in items))

    def test_public_archive_standalone_build_and_install(self):
        archive = package_skill.build('public', package_public_source.entries(), self.tmp)
        extracted = self.tmp / 'public-source'
        with zipfile.ZipFile(archive) as z:
            z.extractall(extracted)
        tool = extracted / 'plugin/hglos/scripts/context_index.py'
        self.assertTrue(tool.is_file())
        p = subprocess.run([sys.executable, '-B', str(tool), 'build', '--layout', 'source',
                            '--root', str(extracted / 'plugin/hglos'), '--task', 'verify'],
                           cwd=extracted, capture_output=True, text=True, encoding='utf-8', env=env(), timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        project = self.tmp / 'project'
        project.mkdir()
        p = subprocess.run([sys.executable, '-B', str(extracted / 'tools/install_skills.py'),
                            '--target', 'codex', '--project', str(project)], cwd=extracted,
                           capture_output=True, text=True, encoding='utf-8', env=env(), timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.cli(project / '.agents/skills/hglos-verify', 'verify')

    def test_entry_and_host_skill_documents_connect_canonical_inputs(self):
        entry = self.text('ENTRY.md')
        for path in ('RULES.md', 'HOST.md', 'COORDINATION.md', 'context_routes.json', 'scripts/context_index.py'):
            self.assertIn(path, entry)
        routes = json.loads(self.text('context_routes.json'))
        self.assertEqual(set(routes), {'schema_version', 'tasks'})
        self.assertEqual(set(routes['tasks']), {'answer', 'design', 'implement', 'verify', 'dashboard', 'coordinate'})
        for path in ('HOST.md', *(f'skills/{s}/SKILL.md' for s in package_skill.SKILLS)):
            with self.subTest(path=path):
                self.assertIn('ENTRY.md', self.text(path))
        readme = (REPO / 'README.md').read_text(encoding='utf-8')
        for text in ('context_index.py', '--layout source', '--layout installed', '--expect-index-sha256'):
            self.assertIn(text, readme)

    def test_common_handoff_fields_independence_and_read_only_contract(self):
        text = self.text('context-handoff.md')
        for pattern in (r'저장소|repository', r'revision|HEAD', r'dirty|로컬 변경', r'SHA-256|sha256',
                        r'정본|canonical', r'작업|task', r'파일.*범위|file.*scope', r'읽기 전용|read.only',
                        r'출력.*위치|output', r'미확인|한계|limitation', r'다시 읽|re.read',
                        r'이전 대화|prior conversation', r'구현자.*결론|implementer.*conclusion',
                        r'상속하지|상속 금지|do not inherit', r'승인.*아니|승인.*증거.*아니|not.*approval'):
            with self.subTest(pattern=pattern):
                self.assertRegex(text, re.compile(pattern, re.I))
        self.assertIn('../../context-handoff.md', self.text('skills/hglos-implement/SKILL.md'))
        for name in ('native-validator.md', 'validator.md'):
            self.assertIn('../../../context-handoff.md', self.text(f'skills/hglos-verify/agents/{name}'))

    def test_external_validator_run_local_tools_out_and_baseline(self):
        text = self.text('skills/hglos-verify/agents/validator.md')
        self.assertNotIn('<repo_path>/validation/_tools', text)
        self.assertNotIn('<repo_path>/validation/integrity.json', text)
        self.assertRegex(text, r'(?m)^.*\$tools\s*=.*<output_dir>/tools')
        self.assertRegex(text, r'(?m)^.*guard_integrity.py.*snapshot.*--out\s+"?<output_dir>/integrity.json')
        self.assertRegex(text, r'(?m)^.*guard_integrity.py.*verify.*--baseline\s+"?<output_dir>/integrity.json')


if __name__ == '__main__':
    unittest.main()
