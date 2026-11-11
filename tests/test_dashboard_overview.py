"""Overview semantics, visual ordering, navigation, and passive source-safe markup."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'plugin/hglos/skills/hglos-dashboard/scripts'))
import dashboard
import visuals
from test_diagram_views import PLAN


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class Overview(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def source(self, name='Plan.md', text=PLAN):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return dashboard.load_source(self.root.resolve(), path)

    def render(self, sources):
        html = dashboard.render_html(self.root, sources, None, 'SYNTHETIC TEST')
        parsed = Markup()
        parsed.feed(html)
        return html, parsed.tags

    def test_overview_counts_match_explicit_states_and_board_counts(self):
        source = self.source()
        html, tags = self.render([source])
        actual = {attrs['data-task-state']: int(attrs['data-count']) for _, attrs in tags if 'data-task-state' in attrs}
        self.assertEqual(actual, dict(active=1, blocked=1, done=1, pending=0, unknown=0))
        board = Counter()
        for _, attrs in tags:
            if 'data-state' in attrs:
                board[attrs['data-state']] += int(attrs['data-count'])
        self.assertEqual(actual, dict(board))
        self.assertIn('완료율/승인 판정 아님', html)
        self.assertEqual(visuals.prepare_view(source)['model']['tasks'][1]['state'], 'active')

    def test_flow_is_before_compact_board_and_each_task_retains_details(self):
        html, tags = self.render([self.source()])
        flow = next(i for i, (_, attrs) in enumerate(tags) if attrs.get('class') == 'flow-panel')
        board = next(i for i, (_, attrs) in enumerate(tags) if attrs.get('class') == 'task-board')
        self.assertLess(flow, board)
        tasks = [(tag, attrs) for tag, attrs in tags if attrs.get('class') == 'task']
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(tag == 'details' and 'open' not in attrs for tag, attrs in tasks))
        for value in ('run/a.log', 'src/b.cs', 'tests/c.py', 'python c.py', '장비 미연결'):
            self.assertIn(value, html)

    def test_multiple_plans_keep_same_task_names_separate_and_all_fragments_resolve(self):
        html, tags = self.render([self.source('One.md'), self.source('Two.md')])
        ids = [attrs['id'] for _, attrs in tags if 'id' in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        links = {attrs['href'][1:] for _, attrs in tags if attrs.get('href', '').startswith('#')}
        self.assertTrue(links <= set(ids), links - set(ids))
        task_ids = [value for value in ids if value.startswith('task-')]
        self.assertEqual(len(task_ids), 6)
        counts = {attrs['data-task-state']: int(attrs['data-count']) for _, attrs in tags if 'data-task-state' in attrs}
        self.assertEqual(counts['done'], 2)
        self.assertIn('One.md', html)
        self.assertIn('Two.md', html)

    def test_cycle_missing_duplicate_malformed_and_large_graph_warn_before_summary(self):
        cases = [PLAN.replace('| A | 없음 |', '| A | C |'),
                 PLAN.replace('| B | A |', '| B | absent |'),
                 PLAN.replace('| B | A | src/b.cs', '| A | A | src/b.cs'),
                 PLAN.replace('| C | ["A", "B"] |', '| C | [42] |'),
                 '## 작업 분할\n| 작업 | after |\n|---|---|\n' + '\n'.join(f'| A{i} | 없음 |' for i in range(25))]
        for text in cases:
            with self.subTest(text=text[:40]):
                source = self.source(text=text)
                prepared = visuals.prepare_view(source)
                self.assertTrue(prepared['model']['graph_errors'])
                html, tags = self.render([source])
                warning = next(attrs for _, attrs in tags if 'data-warning-count' in attrs)
                self.assertGreater(int(warning['data-warning-count']), 0)
                self.assertIn('작업 관계 미검증', html)
                self.assertNotIn('data-from=', html)
                self.assertEqual(sum(attrs.get('class') == 'task' for _, attrs in tags), len(prepared['model']['tasks']))

    def test_unknown_and_empty_inputs_do_not_invent_progress_or_current_action(self):
        text = PLAN.split('## Current State')[0] + '\n## Notes\n전부 완료\n'
        html, tags = self.render([self.source(text=text)])
        counts = {attrs['data-task-state']: int(attrs['data-count']) for _, attrs in tags if 'data-task-state' in attrs}
        self.assertEqual(counts['unknown'], 3)
        self.assertEqual(counts['done'], 0)
        self.assertIn('미기재 · 미검증', html)
        empty, _ = self.render([])
        self.assertIn('작업 집계 미검증', empty)
        self.assertNotIn('100% 완료', empty)

    def test_html_node_links_are_local_but_standalone_svg_has_no_html_fragments(self):
        source = self.source()
        html, tags = self.render([source])
        self.assertTrue(any(tag == 'a' and attrs.get('href', '').startswith('#task-') for tag, attrs in tags))
        standalone = visuals.diagram_sources(visuals.project_model(source))['dependencies'][0]
        self.assertNotIn('href=', standalone)
        ET.fromstring(standalone)
        self.assertNotIn('<script', html)
        self.assertNotIn('foreignObject', html)
        self.assertFalse(any(key.startswith('on') for _, attrs in tags for key in attrs))

    def test_hostile_labels_stay_text_and_no_remote_ui_assets_are_loaded(self):
        hostile = '<img src="https://example.com/a" onerror="oops()">'
        source = self.source('Unsafe.md', PLAN.replace('장비 미연결', hostile))
        html, tags = self.render([source])
        self.assertIn('&lt;img', html)
        self.assertFalse(any(tag in ('script', 'img', 'iframe', 'link') for tag, _ in tags))
        css = (REPO / 'plugin/hglos/skills/hglos-dashboard/scripts/dashboard.css').read_text(encoding='utf-8')
        self.assertNotIn('url(', css)
        self.assertNotIn('@import', css)

    def test_session_only_input_has_resolving_navigation_without_task_owner_inference(self):
        data = {'sessions': [{'id': 'session-a', 'host': 'codex', 'task': 'free text purpose',
                             'state': 'working', 'suspect': False, 'claims': [], 'operations': [],
                             'reason': 'historical Claim conflict', 'last_seen': '2026-10-23T00:00:00+00:00'}],
                'generated': '2026-10-23T00:00:00+00:00', 'sha256': '0' * 64, 'age_seconds': 0}
        html = dashboard.render_html(self.root, [], None, 'SYNTHETIC', data)
        parsed = Markup()
        parsed.feed(html)
        ids = {attrs['id'] for _, attrs in parsed.tags if 'id' in attrs}
        links = {attrs['href'][1:] for _, attrs in parsed.tags if attrs.get('href', '').startswith('#')}
        self.assertTrue(links <= ids)
        self.assertIn('session-a', html)
        self.assertIn('작업 집계 미검증', html)
        self.assertNotIn('data-blocker', html)

    def test_mermaid_keeps_unknown_raw_status_and_normalized_unverified_label(self):
        source = self.source(text=PLAN.replace('| A | 완료 | run/a.log |', '| A | 거의 완료 | run/a.log |'))
        model = visuals.project_model(source)
        code = visuals.diagram_sources(model)['dependencies'][1]
        label = next(line for line in code.splitlines() if 'T0[' in line)
        self.assertIn('거의 완료', label)
        self.assertIn('미검증', label)

    def test_long_korean_node_titles_retain_full_text_without_wide_visible_labels(self):
        name = '기존장비검사결과호환성검증' * 8
        source = self.source(text=PLAN.replace('| A |', '| ' + name + ' |').replace('| B | A |', '| B | ' + name + ' |').replace('["A", "B"]', '["' + name + '", "B"]'))
        prepared = visuals.prepare_view(source)
        self.assertIn(name, prepared['diagrams']['dependencies'][0])
        self.assertIn('…', visuals.short_svg(name))
        self.assertLess(len(visuals.short_svg(name)), 16)
        html, _ = self.render([source])
        self.assertIn(name, html)


if __name__ == '__main__':
    unittest.main()
