"""doc/29 condition 7 (M5): contract-violating rounds are failed rounds, never convergence evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
COMPARE = REPO / 'plugin/hglos/skills/hglos-verify/scripts/compare_rounds.py'
FINDING = {'path': 'src/a.cs', 'line': 3, 'category': 'logic', 'severity': 'high',
           'evidence': 'observed', 'claim': 'Defect', 'detail': 'Why'}
VIOLATION = {'codex_invoked': True, 'contract_violations': ["최상위에 'findings' 키가 없음"], 'findings': []}


class Rounds(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def compare(self, *rounds, cap=3):
        for number, data in enumerate(rounds, 1):
            (self.folder / f'round_{number}.json').write_text(json.dumps(dict(data, round=number), ensure_ascii=False),
                                                             encoding='utf-8')
        result = subprocess.run([sys.executable, str(COMPARE), str(self.folder), '--cap', str(cap)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))
        return result.returncode, result.stdout

    @staticmethod
    def ok(*findings):
        return {'codex_invoked': True, 'contract_violations': [], 'findings': list(findings)}

    def test_violation_rounds_never_converge_as_empty(self):
        code, out = self.compare(VIOLATION, VIOLATION)
        self.assertEqual(code, 1, out)
        self.assertIn('판정불가', out)
        self.assertNotIn('판정: 수렴', out)

    def test_round_without_findings_key_is_failed(self):
        missing = {'codex_invoked': True, 'contract_violations': []}
        code, out = self.compare(missing, missing)
        self.assertEqual(code, 1, out)
        self.assertIn('판정불가', out)

    def test_malformed_violation_field_fails_closed(self):
        malformed = dict(self.ok(), contract_violations='schema mismatch')
        code, out = self.compare(malformed, malformed)
        self.assertEqual(code, 1, out)
        self.assertIn('판정불가', out)

    def test_violation_round_between_successes_is_reported_and_excluded(self):
        code, out = self.compare(self.ok(FINDING), VIOLATION, self.ok(FINDING))
        self.assertEqual(code, 0, out)
        self.assertIn('성공 회차: 2/3', out)
        self.assertIn('실패 회차: 1', out)
        self.assertIn('round 2', out)
        self.assertIn('판정: 수렴 정지', out)
        self.assertNotIn('R2:', out, 'failed round must not appear in the comparison table')

    def test_violation_round_does_not_consume_budget(self):
        other = dict(FINDING, path='src/b.cs', line=9)
        code, out = self.compare(self.ok(FINDING), VIOLATION, self.ok(other))
        self.assertEqual(code, 0, out)
        self.assertIn('판정: 계속', out)

    def test_empty_violation_list_and_legacy_rounds_remain_successful(self):
        legacy = {'codex_invoked': True, 'findings': []}
        code, out = self.compare(self.ok(), legacy)
        self.assertEqual(code, 0, out)
        self.assertIn('판정: 수렴 정지 (발견 없음)', out)


if __name__ == '__main__':
    unittest.main()
