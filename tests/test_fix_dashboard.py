"""doc/29 condition 10 (L2 layout, L3 queue position, L4 links and time) and L7 for hglos-dashboard.

The browser checks at 1024px/375px stay manual (task F); these tests fix the measurable geometry.
"""

import hashlib
import html as htmllib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'plugin/hglos/skills/hglos-dashboard/scripts'
DASH = SCRIPTS / 'dashboard.py'
sys.path.insert(0, str(SCRIPTS))
import dashboard
import visuals
from test_fix_plan_gate import unresolved_references

SVG = '{http://www.w3.org/2000/svg}'
TASKS = [('O 회귀 시험 선작성', '없음'), ('A 세션 조정 수정', 'O 회귀 시험 선작성'),
         ('C 회차 계약 강제', 'O 회귀 시험 선작성'), ('D 무커밋 승인 규칙', 'O 회귀 시험 선작성'),
         ('E 설치 갱신', 'O 회귀 시험 선작성'), ('F 대시보드 배치', 'O 회귀 시험 선작성'),
         ('H 패키징', 'A 세션 조정 수정, C 회차 계약 강제, D 무커밋 승인 규칙, E 설치 갱신, F 대시보드 배치'),
         ('I 통합 검증', 'H 패키징')]


def plan(tasks):
    rows = ''.join(f'| {name} | src/{index} | {after} | tests/t{index}.py | 시험 |\n' for index, (name, after) in enumerate(tasks))
    states = ''.join(f'| {name} | 대기 | |\n' for name, _ in tasks)
    return ('# SYNTHETIC layout plan\n## 작업 분할\n| 작업 | 쓰기 범위 | after | oracle | 검증 |\n|---|---|---|---|---|\n'
            + rows + '## Current State\n- 현재 위치: 구현\n- 다음 한 단계: 검증\n### 작업 현황\n| 작업 | 상태 | 근거 |\n|---|---|---|\n'
            + states)


def source(text):
    return {'name': 'doc/layout.md', 'lines': text.splitlines(), 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def geometry(svg):
    root = ET.fromstring(svg)
    boxes = {}
    for group in root.iter(SVG + 'g'):
        rect = group.find(SVG + 'rect')
        title = group.find(SVG + 'title')
        if rect is not None and rect.get('data-node') == 'true' and title is not None:
            name = title.text.split(' · ')[0]
            boxes[name] = tuple(float(rect.get(k)) for k in ('x', 'y', 'width', 'height'))
    return float(root.get('width')), float(root.get('height')), boxes


class DependencyLayout(unittest.TestCase):
    def layout(self, tasks):
        view = visuals.prepare_view(source(plan(tasks)))
        self.assertEqual(view['model']['graph_errors'], [])
        return view['model'], geometry(view['diagrams']['dependencies'][0])

    def assert_inside_and_ordered(self, model, width, height, boxes, axis=None):
        self.assertEqual(set(boxes), {t['name'] for t in model['tasks']})
        for name, (x, y, w, h) in boxes.items():
            self.assertGreaterEqual(x, 0, name)
            self.assertLessEqual(x + w, width, f'{name} leaves the {width}px drawing')
            self.assertLessEqual(y + h, height, name)
        for task in model['tasks']:
            tx, ty, _, _ = boxes[task['name']]
            for dep in task['after']:
                dx, dy, dw, dh = boxes[dep]
                left, above = dx + dw <= tx, dy + dh <= ty
                self.assertTrue(left or above, f'{dep} must precede {task["name"]}')
                if axis == 'x':
                    self.assertTrue(left, f'{dep} must stay left of {task["name"]}')

    def test_wide_graph_switches_to_vertical_within_1000px(self):
        model, (width, height, boxes) = self.layout(TASKS)
        self.assertLessEqual(width, 1000, 'four dependency layers must not need a 1,200px drawing')
        self.assert_inside_and_ordered(model, width, height, boxes)

    def test_narrow_graph_keeps_horizontal_flow(self):
        model, (width, height, boxes) = self.layout([('A', '없음'), ('B', 'A')])
        self.assertLessEqual(width, 1000)
        self.assert_inside_and_ordered(model, width, height, boxes, axis='x')


class PageOrder(unittest.TestCase):
    def test_status_queues_directly_follow_overview(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            path = root / 'plan.md'
            path.write_text(plan(TASKS), encoding='utf-8')
            page = dashboard.render_html(root, [dashboard.load_source(root, path)], None, 'SYNTHETIC TEST')
        overview = page.index('id="hglos-overview"')
        queues = page.index('class="queues-panel"')
        main = page.index('<main')
        self.assertLess(overview, queues)
        self.assertLess(queues, main, 'state lists belong directly under the summary, before plan cards')
        self.assertNotIn('<article', page[overview:queues])


class OutputLinksAndTime(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.plan = self.root / 'plans/계획 A.md'
        self.plan.parent.mkdir()
        self.plan.write_text(plan(TASKS[:2]), encoding='utf-8')
        self.concerns = self.root / 'ledger/CONCERNS.md'
        self.concerns.parent.mkdir()
        self.concerns.write_text('| ID | 상태 |\n|---|---|\n| CN-1 | OPEN |\n', encoding='utf-8')

    def render(self, output, env=None):
        result = subprocess.run([sys.executable, str(DASH), '--repo', str(self.root), '--plan', str(self.plan),
                                 '--concerns', str(self.concerns), '--output', output],
                                capture_output=True, text=True, encoding='utf-8', timeout=60,
                                env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', **(env or {})))
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.root / output).read_text(encoding='utf-8')

    def test_html_source_links_are_relative_to_output(self):
        page = self.render('out/view/dashboard.html')
        hrefs = [htmllib.unescape(h) for h in re.findall(r'href="([^"#][^"]*)"', page)]
        self.assertFalse([h for h in hrefs if h.lower().startswith('file:')], hrefs)
        base = self.root / 'out/view'
        resolved = {(base / unquote(h)).resolve() for h in hrefs}
        self.assertIn(self.plan.resolve(), resolved)
        self.assertIn(self.concerns.resolve(), resolved)

    def test_markdown_source_links_are_relative_to_output(self):
        text = self.render('out/dashboard.md')
        self.assertNotIn('file:', text)
        self.assertIn('../plans/', text)
        self.assertIn('../ledger/', text)

    def stamp(self, page):
        match = re.search(r'생성:?\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z))', page)
        self.assertIsNotNone(match, 'generated time not found')
        return match.group(1)

    def offset_under(self, tz):
        probe = subprocess.run([sys.executable, '-c', 'from datetime import datetime;'
                                'print(datetime.now().astimezone().isoformat(timespec="seconds"))'],
                               capture_output=True, text=True, timeout=30, env=dict(os.environ, TZ=tz))
        return probe.stdout.strip()[-6:]

    def test_generated_time_uses_runtime_timezone(self):
        for tz, offset in (('XXX-3', '+03:00'), ('UTC0', '+00:00')):
            if self.offset_under(tz) != offset:
                self.skipTest(f'platform ignores TZ={tz}')
            with self.subTest(tz=tz):
                value = self.stamp(self.render(f'tz-{offset[1:3]}.html', {'TZ': tz}))
                self.assertTrue(value.endswith(offset) or (offset == '+00:00' and value.endswith('Z')), value)


class DashboardSkillPaths(unittest.TestCase):
    """Condition 11 (L7) for the hglos-dashboard skill owned by task F."""

    def test_dashboard_skill_references_resolve_in_both_layouts(self):
        self.assertEqual(unresolved_references('hglos-dashboard'), [])


if __name__ == '__main__':
    unittest.main()
