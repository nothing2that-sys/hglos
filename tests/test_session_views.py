"""Ownership views preserve snapshot facts, reject ambiguous links and stay offline."""

import copy
from datetime import datetime, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'plugin/hglos/scripts'))
import coordination as c
import coordination_view as view


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.metrics = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if 'data-metric' in attrs:
            self.metrics[attrs['data-metric']] = int(attrs['data-count'])


class SessionViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        c.initialize(self.root, 'scoped')
        self.path = self.root / '.hglos/runtime/sessions.json'
        self.now = datetime.now(timezone.utc).isoformat()

    def snapshot(self):
        with c.Registry(self.root) as reg:
            reg.register('claude', 'Engine 수정', 'a')
            reg.register('codex', 'UI 수정', 'b')
            reg.claim('a', [('tree', 'src/Engine'), ('resource', 'build')], 1)
            reg.before('b', 'op1', 'Write', [('file', 'src/View.cs')])
            try:
                reg.claim('b', [('resource', 'build')], 1)
            except c.Conflict:
                pass
            return reg.snapshot()

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return view.load_sessions(self.root, self.path)

    def test_model_metrics_reverse_index_and_outputs_agree(self):
        data = self.load(self.snapshot())
        before = copy.deepcopy(data)
        model = view.session_model(data)
        self.assertEqual(model['totals'], dict(sessions=2, claims=3, operations=1, waiting=1, suspect=0))
        self.assertEqual(model['scopes'][('resource', 'build')][0]['id'], 'a')
        html, markdown = view.sessions_html(data), view.sessions_markdown(data)
        parsed = Markup(html)
        self.assertEqual(parsed.metrics, model['totals'])
        for key, value in model['totals'].items():
            self.assertIn(f'{key}={value}', markdown)
        self.assertIn('Write / op1', html)
        self.assertIn('Write / op1', markdown)
        self.assertEqual(data, before)
        cards = [attrs for tag, attrs in parsed.tags if tag == 'details' and attrs.get('class') == 'session-card']
        self.assertEqual(len(cards), 2)
        ids = {attrs['id'] for _, attrs in parsed.tags if 'id' in attrs}
        targets = {attrs['href'][1:] for tag, attrs in parsed.tags if tag == 'a'}
        self.assertTrue(targets <= ids)

    def test_released_peer_does_not_become_inferred_live_blocker(self):
        self.snapshot()
        with c.Registry(self.root) as reg:
            reason = next(r for r in reg.snapshot()['sessions'] if r['id'] == 'b')['reason']
            reg.release('a', 1, quiescent=True)
            data = self.load(reg.snapshot())
        html = view.sessions_html(data)
        self.assertNotIn(('resource', 'build'), view.session_model(data)['scopes'])
        self.assertEqual(next(r for r in data['sessions'] if r['id'] == 'b')['reason'], reason)
        self.assertIn(escape(reason), html)
        self.assertIn('현재 차단자·재시도 결과·해제 허가를 증명하지 않습니다', html)
        self.assertNotIn('data-blocker', html)
        self.assertIn('Claim conflict:', html)

    def test_successful_retry_clears_only_reported_conflict(self):
        self.snapshot()
        with c.Registry(self.root) as reg:
            reg.release('a', 1, quiescent=True)
            reg.claim('b', [('resource', 'build')], 1)
            data = self.load(reg.snapshot())
        model = view.session_model(data)
        self.assertEqual(model['totals']['waiting'], 0)
        self.assertEqual(model['scopes'][('resource', 'build')][0]['id'], 'b')
        self.assertNotIn('Claim conflict:', view.sessions_html(data))

    def test_ended_and_suspect_keep_claims(self):
        data = self.snapshot()
        data['sessions'][0].update(state='ended', suspect=True)
        loaded = self.load(data)
        model = view.session_model(loaded)
        self.assertEqual(model['totals']['claims'], 3)
        self.assertEqual(model['totals']['suspect'], 1)
        self.assertIn('□ 종료 신고 / 연락 불명', view.sessions_html(loaded))

    def test_native_markup_is_passive_and_svg_matches_ownership_only(self):
        data = self.load(self.snapshot())
        html = view.sessions_html(data)
        parsed = Markup(html)
        self.assertFalse(any(tag in ('script', 'iframe', 'canvas', 'foreignobject') for tag, _ in parsed.tags))
        self.assertFalse(any(key.startswith('on') for _, attrs in parsed.tags for key in attrs))
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for row in data['sessions']:
            svg = ET.fromstring(view.ownership_svg(row))
            self.assertEqual(svg.attrib['role'], 'img')
            self.assertTrue(svg.find('s:desc', ns).text)
            self.assertEqual(len(svg.findall('s:path', ns)), len(row['claims']))
            self.assertEqual(len(svg.findall('s:g', ns)), len(row['claims']))

    def test_long_scopes_are_full_in_text_and_large_graph_falls_back(self):
        data = self.snapshot()
        row = data['sessions'][0]
        name = '한글긴이름' * 40
        row['claims'] = [{'kind': 'file', 'scope': name + str(i)} for i in range(13)]
        loaded = self.load(data)
        self.assertEqual(view.ownership_svg(loaded['sessions'][0]), '')
        html, md = view.sessions_html(loaded), view.sessions_markdown(loaded)
        self.assertIn('도식 생략', html)
        for i in range(13):
            self.assertIn(name + str(i), html)
            self.assertIn(name + str(i), md)

    def test_hostile_content_is_text_not_markup_or_links(self):
        data = self.snapshot()
        row = data['sessions'][0]
        row['id'] = '<script onload="bad()">x</script>'
        row['host'] = 'host[link](https://example.com)'
        row['reason'] = '</pre><img src=x onerror="bad()">\nClaim conflict: [truncated'
        for claim in row['claims']:
            claim['owner'] = row['id']
        data['sessions'][1]['operations'][0]['tool'] = '<iframe> | `tool`'
        loaded = self.load(data)
        html, md = view.sessions_html(loaded), view.sessions_markdown(loaded)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<iframe', html)
        self.assertIn('&lt;iframe&gt;', html)
        self.assertIn('&lt;iframe&gt;', md)
        self.assertIn(r'host\[link\]', md)
        self.assertNotIn(row['id'], {a.get('id') for _, a in Markup(html).tags})

    def test_legacy_optional_fields_and_empty_snapshot(self):
        data = self.snapshot()
        for row in data['sessions']:
            row.pop('version')
            for claim in row['claims']:
                claim.pop('owner')
            for operation in row['operations']:
                operation.pop('owner')
        self.assertIn('소유권 세대(version)</dt><dd>미기록', view.sessions_html(self.load(data)))
        data['sessions'] = []
        loaded = self.load(data)
        self.assertIn('등록된 세션 없음', view.sessions_html(loaded))
        self.assertEqual(view.session_model(loaded)['totals']['claims'], 0)
        self.assertIn('선점 기록 없음', view.sessions_markdown(loaded))

    def test_ambiguous_or_invalid_input_is_rejected_before_render(self):
        data = self.snapshot()
        changes = [
            lambda d: d['sessions'].append(copy.deepcopy(d['sessions'][0])),
            lambda d: d['sessions'][0]['claims'][0].update(owner='b'),
            lambda d: d['sessions'][1]['operations'][0].update(owner='a'),
            lambda d: d['sessions'][1].update(operations=['not an operation']),
            lambda d: d['sessions'][1]['operations'][0].update(started='not a time'),
            lambda d: d['sessions'][0].update(last_seen='2026-10-22T01:00:00'),
            lambda d: d.update(generated=None),
            lambda d: d.update(schema=True),
            lambda d: d['sessions'][0].update(version=True),
            lambda d: d['sessions'][0]['claims'].append(copy.deepcopy(d['sessions'][0]['claims'][0])),
            lambda d: d['sessions'][1]['operations'].append(copy.deepcopy(d['sessions'][1]['operations'][0])),
            lambda d: d['sessions'][0]['claims'][0].update(kind='unknown'),
        ]
        for change in changes:
            bad = copy.deepcopy(data)
            change(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load(bad)

    def test_cli_failure_does_not_replace_existing_output_then_success_preserves_source(self):
        data = self.snapshot()
        output = self.root / 'dashboard.html'
        output.write_text('user report', encoding='utf-8')
        data['sessions'][0]['claims'][0]['owner'] = 'b'
        self.path.write_text(json.dumps(data), encoding='utf-8')
        script = REPO / 'plugin/hglos/skills/hglos-dashboard/scripts/dashboard.py'
        command = [sys.executable, str(script), '--repo', str(self.root), '--sessions', str(self.path), '--output', str(output), '--overwrite']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_text(), 'user report')
        data['sessions'][0]['claims'][0]['owner'] = 'a'
        self.path.write_text(json.dumps(data), encoding='utf-8')
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        output.unlink()
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        html = output.read_text(encoding='utf-8')
        self.assertIn("default-src 'none'", html)
        self.assertNotIn('<script', html)


if __name__ == '__main__':
    unittest.main()
