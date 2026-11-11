"""doc/29 condition 8 (M4) and the L7 part of condition 11 for design/implement/verify.

plan_hash.py proves "no change outside Current State since approval" where commits are forbidden.
Also hosts unresolved_references(), reused by the coordinate/dashboard path checks.
"""

import importlib.util
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / 'plugin/hglos'
PLAN_HASH = PLUGIN / 'scripts/plan_hash.py'
sys.path.insert(0, str(REPO / 'tools'))
import package_skill

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')

PLAN = """# Slice Plan - SYNTHETIC
**승인**: 범위 JH / 2026-10-23 / "범위 승인" · 계획 JH / 2026-10-23 / "계획 승인"

## 1. 닫는 운영 주장
예시 계획서다.

```md
## Current State
- 이 펜스는 예시이며 실제 절이 아니다
```
펜스 뒤 본문도 승인 대상이다.

## 2. 종결 조건
| # | 조건 | 판정 수단 |
|---|---|---|
| 1 | 시험 통과 | python -m unittest |

## Current State
- 승인 시점 커밋: 커밋 금지 환경
- 현재 위치: 승인 완료
- 다음 한 단계: 구현 착수

### 작업 현황
| 작업 | 상태 | 근거 |
|---|---|---|
| A | 대기 | |

## 부록
- Current State 뒤의 절도 승인 대상이다.
"""


def unresolved_references(skill):
    """HOST/RULES/COORDINATION mentions in SKILL.md lacking a path valid in each install layout.

    Installed bundle: the file sits at the skill root (package_skill.entries).
    Claude plugin: the skill runs from plugin/hglos/skills/<skill>/, files live two levels up.
    A mention passes when some backticked path ending in that name resolves in BOTH layouts
    (one path per layout is enough).
    """
    skill_dir = (PLUGIN / 'skills' / skill).resolve()
    text = (skill_dir / 'SKILL.md').read_text(encoding='utf-8')
    arcs = {arc for arc, _ in package_skill.entries(skill)}
    quoted = re.findall(r'`([^`\s]+)`', text)
    missing = []
    for name in ('HOST.md', 'RULES.md', 'COORDINATION.md'):
        if name not in text:
            continue
        candidates = [q for q in quoted if q == name or q.endswith('/' + name)]
        plugin_ok = any((skill_dir / q).resolve().is_file() and (skill_dir / q).resolve().is_relative_to(PLUGIN.resolve())
                        for q in candidates)
        installed_ok = any(posixpath.normpath(f'{skill}/{q}') in arcs for q in candidates)
        if not plugin_ok:
            missing.append((name, 'plugin'))
        if not installed_ok:
            missing.append((name, 'installed'))
    return missing


def load_plan_hash():
    if not PLAN_HASH.is_file():
        raise AssertionError(f'missing {PLAN_HASH.relative_to(REPO)}')
    spec = importlib.util.spec_from_file_location('plan_hash', PLAN_HASH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(path, *args):
    return subprocess.run([sys.executable, str(PLAN_HASH), str(path), *args], capture_output=True,
                          text=True, encoding='utf-8', env=ENV, timeout=30)


class PlanHash(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.ph = load_plan_hash()

    def digest(self, text):
        return self.ph.digest(text)

    def write(self, text, name='plan.md', newline='\n'):
        path = self.folder / name
        path.write_bytes(text.replace('\n', newline).encode('utf-8'))
        return path

    def test_hash_is_stable_across_runs_cli_and_line_endings(self):
        value = self.digest(PLAN)
        self.assertRegex(value, r'^[0-9a-f]{64}$')
        self.assertEqual(value, self.digest(PLAN))
        self.assertEqual(value, self.digest(PLAN.replace('\n', '\r\n')))
        for newline in ('\n', '\r\n'):
            result = cli(self.write(PLAN, newline=newline))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), value)

    def test_current_state_edits_do_not_change_hash(self):
        value = self.digest(PLAN)
        edits = [
            PLAN.replace('- 현재 위치: 승인 완료', '- 현재 위치: 구현 중'),
            PLAN.replace('| A | 대기 | |', '| A | 완료 | report-A.md |'),
            PLAN.replace('- 승인 시점 커밋: 커밋 금지 환경', '- 승인 시점 커밋: 커밋 금지 환경\n- 승인 시점 해시: ' + value),
        ]
        for text in edits:
            with self.subTest(text=text[-80:]):
                self.assertEqual(self.digest(text), value)

    def test_changes_outside_current_state_change_hash(self):
        value = self.digest(PLAN)
        edits = {
            'approval': PLAN.replace('"계획 승인"', '"계획 승인함"'),
            'condition': PLAN.replace('| 1 | 시험 통과 |', '| 1 | 시험 일부 통과 |'),
            'after fenced example': PLAN.replace('펜스 뒤 본문도 승인 대상이다.', '펜스 뒤 본문을 바꿨다.'),
            'section after Current State': PLAN.replace('절도 승인 대상이다.', '절은 승인 대상이 아니다.'),
            'whitespace': PLAN.replace('예시 계획서다.', '예시 계획서다. '),
        }
        for label, text in edits.items():
            with self.subTest(label=label):
                self.assertNotEqual(text, PLAN)
                self.assertNotEqual(self.digest(text), value)

    def test_moving_text_across_current_state_changes_hash(self):
        head, appendix = PLAN.split('## 부록\n')
        before_state, state = head.rsplit('## Current State\n', 1)
        moved = before_state + '## 부록\n' + appendix + '\n## Current State\n' + state
        self.assertNotEqual(self.digest(moved), self.digest(PLAN))

    def test_missing_or_duplicate_current_state_is_an_error(self):
        missing = PLAN.replace('## Current State\n- 승인', '## 현황 메모\n- 승인')
        duplicate = PLAN + '\n## Current State\n- 두 번째\n'
        for label, text in (('missing', missing), ('duplicate', duplicate)):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.digest(text)
                self.assertEqual(cli(self.write(text, label + '.md')).returncode, 2)

    def test_cli_expect_rejects_mismatch_before_accepting_match(self):
        path = self.write(PLAN)
        value = self.digest(PLAN)
        wrong = ('0' if value[0] != '0' else '1') + value[1:]
        self.assertEqual(cli(path, '--expect', wrong).returncode, 1)
        self.assertEqual(cli(path, '--expect', value).returncode, 0)
        path.write_text(PLAN.replace('| 1 | 시험 통과 |', '| 1 | 바뀐 조건 |'), encoding='utf-8')
        self.assertEqual(cli(path, '--expect', value).returncode, 1)
        self.assertEqual(cli(self.folder / 'absent.md', '--expect', value).returncode, 2)

    def test_plan_hash_ships_in_every_skill_bundle(self):
        load_plan_hash()
        for skill in package_skill.SKILLS:
            with self.subTest(skill=skill):
                self.assertIn(f'{skill}/scripts/plan_hash.py', {arc for arc, _ in package_skill.entries(skill)})


def rule_lines(path):
    lines = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if 'plan_hash.py' in line:
            line = re.sub(r'^\s*(?:[-*>]|\d+\.)\s*', '', line)
            lines.add(' '.join(line.split()))
    return lines


def section(text, title):
    match = re.search(r'^(#+)\s+' + re.escape(title) + r'.*$', text, re.MULTILINE)
    if not match:
        return ''
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(r'^#{1,%d}\s' % level, rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


class NoCommitRule(unittest.TestCase):
    DOCS = {'HOST': PLUGIN / 'HOST.md',
            'design': PLUGIN / 'skills/hglos-design/SKILL.md',
            'implement': PLUGIN / 'skills/hglos-implement/SKILL.md'}

    def test_three_documents_share_one_plan_hash_rule_line(self):
        found = {name: rule_lines(path) for name, path in self.DOCS.items()}
        for name, lines in found.items():
            self.assertTrue(lines, f'{name} does not mention plan_hash.py')
        shared = set.intersection(*found.values())
        self.assertTrue(shared, 'design, implement and HOST must carry the same plan_hash.py rule sentence')
        self.assertTrue(any('Current State' in line for line in shared))

    def test_implement_start_conditions_include_no_commit_path(self):
        text = self.DOCS['implement'].read_text(encoding='utf-8')
        self.assertIn('plan_hash.py', section(text, '시작 조건'))

    def test_broad_work_request_is_not_reused_as_plan_approval(self):
        for name in ('HOST', 'implement'):
            with self.subTest(doc=name):
                self.assertIn('계획 승인으로 재사용하지 않는다', self.DOCS[name].read_text(encoding='utf-8'))


class SkillPaths(unittest.TestCase):
    """Condition 11 (L7) for the skills owned by task D."""

    def test_design_implement_verify_references_resolve_in_both_layouts(self):
        for skill in ('hglos-design', 'hglos-implement', 'hglos-verify'):
            with self.subTest(skill=skill):
                self.assertEqual(unresolved_references(skill), [])


if __name__ == '__main__':
    unittest.main()
