"""Regression checks for generated report locations and raw approval provenance."""

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
ROUND = REPO / 'plugin/hglos/skills/hglos-verify/scripts/make_round_json.py'
DASHBOARD = REPO / 'plugin/hglos/skills/hglos-dashboard/scripts/dashboard.py'


def run(script, root, *args):
    return subprocess.run([sys.executable, '-B', str(script), *map(str, args)], cwd=root,
                          capture_output=True, text=True, encoding='utf-8',
                          env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))


class ReportLocations(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.raw = self.root / 'native.raw.json'
        self.raw.write_text('{"findings": []}', encoding='utf-8')

    def assert_report_location(self, directory, expected_exit=0, *extra):
        result = run(ROUND, self.root, self.raw, '--round', 7, '--outdir', directory, *extra)
        self.assertEqual(result.returncode, expected_exit, result.stderr)
        output = self.root / directory
        metadata = json.loads((output / 'round_7.json').read_text(encoding='utf-8'))
        resolved = (self.root / metadata['report_path']).resolve()
        actual = (output / 'round_7.md').resolve()
        self.assertEqual(resolved, actual)
        self.assertTrue(resolved.is_file())
        self.assertIn('# 적대적 검증 보고서 - round 7', resolved.read_text(encoding='utf-8'))
        return metadata

    def test_report_path_locates_default_nested_relative_and_absolute_output(self):
        for directory in ('validation', 'runs/실행 결과', self.root / '절대 경로'):
            with self.subTest(directory=directory):
                self.assert_report_location(directory)

    def test_failed_review_still_links_its_actual_report(self):
        metadata = self.assert_report_location('failed/review', 1, '--exit-code', 2)
        self.assertFalse(metadata['codex_invoked'])
        self.assertEqual(metadata['findings'], [])


class PreformattedText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_pre = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.in_pre = True

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.in_pre = False

    def handle_data(self, data):
        if self.in_pre:
            self.text.append(data)


class ApprovalProvenance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def generate(self, sources):
        before = {name: content.encode('utf-8') for name, content in sources.items()}
        for name, content in before.items():
            (self.root / name).write_bytes(content)
        outputs = {}
        for kind in ('html', 'md'):
            args = ['--repo', self.root, '--output', f'dashboard.{kind}']
            for name in sources:
                args += ['--plan', name]
            result = run(DASHBOARD, self.root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs[kind] = (self.root / f'dashboard.{kind}').read_text(encoding='utf-8')
        for name, original in before.items():
            self.assertEqual((self.root / name).read_bytes(), original)
        return outputs

    def test_both_formats_preserve_each_plans_approval_text_and_line_numbers(self):
        approval_a = '**승인**: 범위 <미기입> · 계획 <미기입>'
        approval_b = '계획 승인: "SYNTHETIC" </pre><script>example()</script> `raw`'
        outputs = self.generate({
            'A.md': f'# Plan A\n{approval_a}\n',
            'B.md': f'# Plan B\n\n{approval_b}\n',
        })
        html = PreformattedText()
        html.feed(outputs['html'])
        for line, approval in ((2, approval_a), (3, approval_b)):
            numbered = f'L{line}: {approval}'
            self.assertIn(numbered, '\n'.join(html.text))
            # An indented code block keeps Markdown/HTML characters visible as raw source.
            self.assertIn(f'    {numbered}', outputs['md'])
        self.assertNotIn('<script>example()</script>', outputs['html'])
        self.assertEqual(outputs['md'].count('### 승인 관련 원문 · 유효성 미검증'), 2)
        self.assertIn('## A.md', outputs['md'])
        self.assertIn('## B.md', outputs['md'])

    def test_missing_approval_remains_explicitly_unverified_in_both_formats(self):
        outputs = self.generate({'empty.md': '# Plan without approval\n'})
        for content in outputs.values():
            self.assertIn('해당 원문 없음 · 미검증', content)


if __name__ == '__main__':
    unittest.main()
