"""doc/35 O oracle - verifier rank table with Claude Fable 5.2 (doc/35 0.3, 6.1, 6.2).

NOT FROZEN until JH confirms this file by its test list, test count and SHA-256.

Contract fixed here (doc/35 6.1, decisions D1-D4, doc/02 J5):
  cross_host.rank_status(observed, implementer) -> (rank or None, 'ranked' | 'excluded' | 'unknown_grade')
  returns every cell of the doc/35 0.3 table below. Anthropic names compare by claude_base
  (agy suffix forms such as -high share the row/column of the bare name).

  observed \\ implementer        claude-opus-5-5   claude-fable-5-2   gpt-6.1-sol
  claude-fable-5-2              2 (D2)            3                  1 (D1)
  claude-opus-5-5               3                 excluded (D3)      1
  claude-sonnet-5-5             excluded          excluded           1
  claude-haiku-5-5              excluded          excluded           excluded
  gpt-6.1-sol                   1                 1                  3
  SIMILAR_GRADE['gpt-6.1-sol']  1                 1                  2
  other OpenAI model            1                 1                  unknown_grade
  gemini-*, gpt-oss-*           excluded          excluded           excluded

  Integration (doc/35 2 condition 2), using the existing fixtures read-only:
    cross_host.py claude path: Fable observed for a SOL implementer -> formal, rank 1, round_1.json,
      header '순위: 1'; for an Opus implementer -> formal, rank 2, round_1.json, header '순위: 2';
      Opus observed for a Fable implementer -> identity_unconfirmed, no round_1.json, exit != 0.
    reviewgate_bridge.py review claude-cli=claude-fable-5-2: SOL implementer -> formal_round true,
      header '순위: 1'; Opus implementer -> formal_round true, header '순위: 2'.

Run only through discover (tests/ has no __init__.py):
  python -B -m unittest discover -s tests -p test_verifier_rank.py -v
The harness classes of tests/test_cross_host.py and tests/test_reviewgate_bridge.py are reused by
importing those modules (never their test classes), so no frozen test is collected twice and
neither frozen file is changed. All identifiers are synthetic.
"""
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

import test_cross_host as ch
import test_reviewgate_bridge as rg

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'plugin/hglos/scripts'))
import cross_host  # noqa: E402

FABLE, OPUS, SONNET, HAIKU = 'claude-fable-5-2', 'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5'
SOL = 'gpt-6.1-sol'
LISTED_OPENAI = 'gpt-6.1-example'  # synthetic name put into SIMILAR_GRADE[SOL] only inside one test

R1, R2, R3 = (1, 'ranked'), (2, 'ranked'), (3, 'ranked')
EX, UG = (None, 'excluded'), (None, 'unknown_grade')

# doc/35 0.3, written out cell by cell (not derived from the rule).
# observed -> {implementer column: expected}
TABLE = {
    FABLE: {OPUS: R2, FABLE: R3, SOL: R1},
    OPUS: {OPUS: R3, FABLE: EX, SOL: R1},
    SONNET: {OPUS: EX, FABLE: EX, SOL: R1},
    HAIKU: {OPUS: EX, FABLE: EX, SOL: EX},
    SOL: {OPUS: R1, FABLE: R1, SOL: R3},
    'gpt-6-luna': {OPUS: R1, FABLE: R1, SOL: UG},
    'gpt-6.2-terra': {OPUS: R1, FABLE: R1, SOL: UG},
    'gemini-3.8-flash-low': {OPUS: EX, FABLE: EX, SOL: EX},
    'gemini-4-argon-low': {OPUS: EX, FABLE: EX, SOL: EX},
    'gpt-oss-120b-medium': {OPUS: EX, FABLE: EX, SOL: EX},
}
LISTED_ROW = {OPUS: R1, FABLE: R1, SOL: R2}
ANTHROPIC = (FABLE, OPUS, SONNET, HAIKU)
# Implementer forms: each column's bare name, plus the agy suffix form for the Anthropic columns.
IMPLEMENTERS = ((OPUS, OPUS), (OPUS + '-high', OPUS), (FABLE, FABLE), (FABLE + '-high', FABLE), (SOL, SOL))


def ident(text):
    return re.sub(r'[^0-9A-Za-z]+', '_', text)


def rank_cells():
    """(observed, implementer, expected) for every cell, observed suffix forms included."""
    for row, cols in TABLE.items():
        forms = (row, row + '-high') if row in ANTHROPIC else (row,)
        for observed in forms:
            for implementer, column in IMPLEMENTERS:
                yield observed, implementer, cols[column]


class RankTable(unittest.TestCase):
    """One test per (observed, implementer) cell, so a failure names the cell."""


def _rank_test(observed, implementer, expected):
    def test(self):
        self.assertEqual(cross_host.rank_status(observed, implementer), expected)
        self.assertEqual(cross_host.rank_of(observed, implementer), expected[0])
    return test


def _listed_test(implementer, expected):
    def test(self):
        listed = dict(cross_host.SIMILAR_GRADE)
        listed[SOL] = tuple(listed.get(SOL, ())) + (LISTED_OPENAI,)
        with mock.patch.dict(cross_host.SIMILAR_GRADE, listed):
            self.assertEqual(cross_host.rank_status(LISTED_OPENAI, implementer), expected)
        # Outside the patch the same name is an unlisted OpenAI model again.
        self.assertEqual(cross_host.rank_status(LISTED_OPENAI, implementer),
                         UG if expected == R2 else expected)
    return test


for _obs, _imp, _exp in rank_cells():
    setattr(RankTable, f'test_rank__{ident(_obs)}__by__{ident(_imp)}', _rank_test(_obs, _imp, _exp))
for _imp, _col in IMPLEMENTERS:
    setattr(RankTable, f'test_rank__similar_grade_listed_openai__by__{ident(_imp)}',
            _listed_test(_imp, LISTED_ROW[_col]))


class CrossHostRank(ch.CrossHostCase):
    """cross_host.py claude path with the existing fake CLI (tests/fixtures/cross_host/fake_cli.py)."""

    def assert_formal(self, run, impl, rank):
        proc = self.call('claude', run=run, impl=impl, model=FABLE)
        self.assert_ok(proc)
        r = self.callj(run=run)
        self.assertEqual((r['model_requested'], r['model_observed'], r['model_check']), (FABLE, FABLE, '일치'))
        self.assertIs(r['formal'], True, r.get('formal_reason'))
        self.assertEqual(r['rank'], rank)
        self.assertEqual(r['round_json'], 'round_1.json')
        self.assertTrue((self.rundir(run) / 'round_1.json').is_file())
        self.assertIn(f'순위: {rank} ·', r['header'])
        self.assertNotIn('정식 회차 아님', r['header'])

    def test_fable_for_sol_implementer_is_formal_rank_1(self):
        self.assert_formal('fable-sol', SOL, 1)

    def test_fable_for_opus_implementer_is_formal_rank_2(self):
        self.assert_formal('fable-opus', OPUS, 2)

    def test_opus_for_fable_implementer_is_not_formal(self):
        run = 'opus-fable'
        proc = self.call('claude', run=run, impl=FABLE, model=OPUS)
        r = self.assert_failed(proc, 'identity_unconfirmed', run=run)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual((r['model_observed'], r['model_check']), (OPUS, '일치'))
        self.assertIsNone(r['rank'])
        self.assertFalse((self.rundir(run) / 'round_1.json').exists())
        self.assertIn('순위: 제외', r['header'])


class BridgeRank(rg.BridgeCase):
    """reviewgate_bridge.py review with the existing fake ReviewGate (tests/fixtures/reviewgate/)."""

    def assert_formal(self, impl, rank):
        self.reset()
        mi = rg.identity('claude-cli', FABLE, FABLE, source='claude')
        self.scenario(review=[rg.review_resp([rg.attempt(0, mi, 'claude-cli')])], export=[rg.export_resp()])
        self.assert_cls(self.review(f'claude-cli={FABLE}', impl=impl), 'ok', 0)
        record = self.callj()
        self.assertEqual(record['model_check'], '일치')
        self.assertEqual(record['model_observed'], FABLE)
        self.assertIs(record['formal_round'], True, record.get('formal_reasons'))
        self.assertEqual(record['rank'], rank)
        self.assertTrue((self.rundir / 'round_1.json').is_file())
        self.assertIn(f'순위: {rank} ·', record['header'])
        self.assertNotIn('정식 회차 아님', record['header'])

    def test_fable_for_sol_implementer_is_formal_rank_1(self):
        self.assert_formal(SOL, 1)

    def test_fable_for_opus_implementer_is_formal_rank_2(self):
        self.assert_formal(OPUS, 2)


if __name__ == '__main__':
    unittest.main()
