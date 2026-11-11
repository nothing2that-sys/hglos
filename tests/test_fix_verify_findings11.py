"""Regression tests for verification finding U1 (validation/fix-20261023/verify-20261023-13).

JH 2026-10-26: "U1은 report-I13.md 3절 선택지 1(승인란을 먼저 채운 뒤 해시를 별도 답변으로 확인받는 2단계)로 고치고
수정분에 대해 새 독립 검증 run을 돌려 주세요."
Without commits: fill the approval field first, then hash, then the person confirms that hash in a separate reply.
"""

import importlib.util
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / 'plugin/hglos/skills'
FILLED_FIRST = '승인란을 먼저 채운'
SEPARATE_REPLY = '별도 답변'


def read(relative):
    return (REPO / relative).read_text(encoding='utf-8')


class U1TwoStepOrder(unittest.TestCase):
    def test_design_fills_approval_before_hash_confirmation(self):
        text = read('plugin/hglos/skills/hglos-design/SKILL.md')
        self.assertIn(FILLED_FIRST, text)
        self.assertIn(SEPARATE_REPLY, text)
        self.assertLess(text.index(FILLED_FIRST), text.index(SEPARATE_REPLY), 'approval is recorded before hashing')
        self.assertNotIn('사람이 승인 답변에서', text, 'the hash is no longer quoted in the approval reply')

    def test_implement_uses_the_separately_confirmed_hash(self):
        text = read('plugin/hglos/skills/hglos-implement/SKILL.md')
        self.assertIn(SEPARATE_REPLY, text)
        self.assertIn('승인란을 채운 뒤', text)

    def test_template_names_the_order(self):
        line = next(l for l in read('plugin/hglos/skills/hglos-design/assets/slice-plan-template.md').splitlines()
                    if '승인 시점 해시' in l)
        self.assertIn('승인란을 채운 뒤', line)
        self.assertIn(SEPARATE_REPLY, line)


class U1HashBehaviourJustifiesTheOrder(unittest.TestCase):
    """The documented order works with plan_hash: approval text changes the hash, Current State edits do not."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('plan_hash', REPO / 'plugin/hglos/scripts/plan_hash.py')
        self.ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ph)

    def test_hash_taken_after_filling_approval_survives_current_state_records(self):
        unapproved = '# P\n**승인**: 범위 <미기입> · 계획 <미기입>\n## 1. 주장\n본문\n## Current State\n- 승인 시점 커밋: 커밋 금지 환경\n'
        approved = unapproved.replace('범위 <미기입> · 계획 <미기입>', '범위 JH / 2026-10-26 / "승인" · 계획 JH / 2026-10-26 / "승인"')
        self.assertNotEqual(self.ph.digest(unapproved), self.ph.digest(approved), 'hashing before approval would go stale')
        confirmed = self.ph.digest(approved)
        recorded = approved + f'- 승인 시점 해시: {confirmed} (참고용)\n- 현재 위치: 구현\n'
        self.assertEqual(self.ph.digest(recorded), confirmed)


if __name__ == '__main__':
    unittest.main()
