"""V1: changing a confirmed plan requires gate 2 approval before hashing again."""

from pathlib import Path
import unittest


class V1ChangedPlanApproval(unittest.TestCase):
    def test_changed_plan_returns_to_gate_two_before_hash_confirmation(self):
        path = Path(__file__).resolve().parent.parent / 'plugin/hglos/skills/hglos-design/SKILL.md'
        text = path.read_text(encoding='utf-8')
        start = text.index('3) 확인 뒤 `Current State` 밖을')
        end = text.index('구현 레그는', start)
        procedure = text[start:end]
        required = ('바뀐 계획서를 다시 보여 주고', '게이트 2 승인을 받아', '승인란을 갱신한 뒤', '1)부터 다시 한다')
        positions = [procedure.index(step) if step in procedure else -1 for step in required]
        self.assertTrue(all(position >= 0 for position in positions),
                        'changed plans must be shown, approved at gate 2, recorded, then rehashed')
        self.assertEqual(positions, sorted(positions), 'reapproval must precede a new hash confirmation')
        self.assertNotIn('바꾸면 2)를 다시 한다', procedure)


if __name__ == '__main__':
    unittest.main()
