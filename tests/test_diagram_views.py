"""Visual semantics: explicit state, graph integrity, source fidelity and export safety."""

import hashlib
from html.parser import HTMLParser
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parent.parent / "plugin/hglos/skills/hglos-dashboard/scripts"
sys.path.insert(0, str(SCRIPTS))
import visuals
import dashboard

PLAN = """# UI flow
## 종결 조건
| 조건 | 판정 수단 | 판정 | 근거 |
|---|---|---|---|
| compatible | python check.py | 미검증 | |
## 작업 분할
| 작업 | after | 쓰기 범위 | oracle | 검증 |
|---|---|---|---|---|
| A | 없음 | src/a.cs | tests/a.py | python a.py |
| B | A | src/b.cs | tests/b.py | python b.py |
| C | ["A", "B"] | src/c.cs | tests/c.py | python c.py |
## Current State
- 현재 위치: 구현
- 다음 한 단계: 장비 연결
- 결정 필요: 실행 일정
- 확인된 사실: 파일 변경
### 단계 현황
| 단계 | 상태 | 근거 |
|---|---|---|
| 설계 | 완료 | 승인 문서 위치 |
| 구현 | 진행 중 | 작업 보고 |
| 검증 | 미검증 | 로그 없음 |
### 작업 현황
| 작업 | 상태 | 근거 |
|---|---|---|
| A | 완료 | run/a.log |
| B | 진행 중 | 코드 변경 |
| C | 차단 | 장비 미연결 |
"""


def source(text=PLAN):
    return {"name": "doc/Plan--A.md", "lines": text.splitlines(), "sha256": hashlib.sha256(text.encode()).hexdigest()}


class Model(unittest.TestCase):
    def test_explicit_state_and_task_provenance(self):
        model = visuals.project_model(source())
        self.assertEqual([t["state"] for t in model["tasks"]], ["done", "active", "blocked"])
        self.assertEqual(model["phases"]["설계"]["raw"], "완료")
        self.assertEqual(model["tasks"][2]["after"], ["A", "B"])
        self.assertEqual(model["graph_errors"], [])
        self.assertEqual(model["tasks"][0]["oracle"], "tests/a.py")
        self.assertGreater(model["tasks"][0]["line"], model["tasks"][0]["definition_line"])

    def test_prose_and_outside_snapshot_do_not_set_state(self):
        text = PLAN.split("## Current State")[0] + "\n## Notes\n구현 완료\n### 작업 현황\n| 작업 | 상태 |\n|---|---|\n| A | 완료 |\n"
        model = visuals.project_model(source(text))
        self.assertTrue(all(t["state"] == "unknown" for t in model["tasks"]))
        self.assertEqual(model["phases"], {})

    def test_fenced_examples_not_actual_records(self):
        model = visuals.project_model(source("```md\n" + PLAN + "\n```"))
        self.assertEqual(model["tasks"], [])
        self.assertEqual(model["phases"], {})
        self.assertEqual(visuals.resume_fields(("```md\n" + PLAN + "\n```").splitlines()), {})

    def test_hidden_html_comment_cannot_supply_status_or_resume(self):
        visible = "## 작업 분할\n| 작업 | after |\n|---|---|\n| A | 없음 |\n"
        hidden = "<!--\n" + PLAN + "\n-->"
        model = visuals.project_model(source(visible + hidden))
        self.assertEqual(len(model["tasks"]), 1)
        self.assertEqual(model["tasks"][0]["state"], "unknown")
        self.assertEqual(model["phases"], {})
        self.assertEqual(visuals.resume_fields((visible + hidden).splitlines()), {})
        masked = visuals.readable_lines((visible + hidden).splitlines())
        self.assertEqual(len(masked), len((visible + hidden).splitlines()))

    def test_comment_literal_inside_fence_does_not_hide_later_real_state(self):
        text = '```python\nprint("<!--")\n```\n' + PLAN
        model = visuals.project_model(source(text))
        self.assertEqual(model["tasks"][0]["state"], "done")

    def test_inline_code_comment_token_does_not_hide_state_tables(self):
        for token in ("`<!--`", "`` ` <!-- ``"):
            with self.subTest(token=token):
                text = PLAN.replace("- 확인된 사실: 파일 변경", f"- 확인된 사실: {token} 토큰 처리 구현")
                model = visuals.project_model(source(text))
                self.assertEqual(model["tasks"][0]["state"], "done")
                self.assertEqual(model["phases"]["설계"]["raw"], "완료")
                line = model["tasks"][0]["line"]
                self.assertIn("run/a.log", text.splitlines()[line - 1])
                self.assertIn(token, visuals.resume_fields(text.splitlines())["확인된 사실"][0])

    def test_skip_layer_connection_routes_above_other_task_boxes(self):
        text = "## 작업 분할\n| 작업 | after |\n|---|---|\n| A | 없음 |\n| X | 없음 |\n| B | X |\n| C | A,B |\n"
        svg = ET.fromstring(visuals.dependencies_svg(visuals.project_model(source(text))))
        ns = {"s": "http://www.w3.org/2000/svg"}
        edge = next(p for p in svg.findall("s:path", ns) if p.get("data-from") == "A" and p.get("data-to") == "C")
        self.assertEqual(edge.get("data-route"), "bypass")
        top_route = int(re.search(r"V(\d+) H", edge.get("d")).group(1))
        node_tops = [int(p.get("y")) for p in svg.findall(".//s:rect", ns) if p.get("data-node")]
        self.assertLess(top_route, min(node_tops))
        self.assertEqual(len(svg.findall("s:path", ns)), 3)

    def test_unknown_status_stays_unknown_with_raw_value(self):
        model = visuals.project_model(source(PLAN.replace("| A | 완료 | run/a.log |", "| A | 거의 완료 | run/a.log |")))
        self.assertEqual(model["tasks"][0]["state"], "unknown")
        self.assertEqual(model["tasks"][0]["raw"], "거의 완료")

    def test_duplicate_status_does_not_choose_latest(self):
        text = PLAN + "| A | 대기 | conflicting |\n"
        model = visuals.project_model(source(text))
        self.assertEqual(model["tasks"][0]["state"], "unknown")
        self.assertTrue(any("중복 작업 상태" in w for w in model["warnings"]))

    def test_unknown_predecessor_suppresses_graph_not_task_board(self):
        model = visuals.project_model(source(PLAN.replace("| B | A |", "| B | missing |")))
        diagrams = visuals.diagram_sources(model)
        self.assertTrue(any("missing" in w for w in model["graph_errors"]))
        self.assertIn("UNKNOWN", diagrams["dependencies"][1])
        self.assertNotIn("-->", diagrams["dependencies"][1])
        self.assertEqual(len(model["tasks"]), 3)

    def test_cycle_suppresses_both_svg_and_mermaid(self):
        model = visuals.project_model(source(PLAN.replace("| A | 없음 |", "| A | C |")))
        diagrams = visuals.diagram_sources(model)
        self.assertTrue(any("순환" in w for w in model["graph_errors"]))
        self.assertIn("미검증", diagrams["dependencies"][0])
        self.assertNotIn("-->", diagrams["dependencies"][1])

    def test_duplicate_task_id_suppresses_ambiguous_edges(self):
        model = visuals.project_model(source(PLAN.replace("| B | A | src/b.cs", "| A | A | src/b.cs")))
        self.assertTrue(any("중복 작업 ID" in w for w in model["graph_errors"]))
        self.assertNotIn("-->", visuals.diagram_sources(model)["dependencies"][1])

    def test_dependency_direction_and_labels(self):
        diagrams = visuals.diagram_sources(visuals.project_model(source()))
        self.assertIn("T0 -->|선행| T1", diagrams["dependencies"][1])
        self.assertIn("T1 -->|선행| T2", diagrams["dependencies"][1])
        self.assertNotIn("T2 -->|선행| T0", diagrams["dependencies"][1])
        ET.fromstring(diagrams["dependencies"][0])
        ET.fromstring(diagrams["workflow"][0])

    def test_unreadable_dependency_is_not_treated_as_no_predecessor(self):
        model = visuals.project_model(source(PLAN.replace('| C | ["A", "B"] |', '| C | [42] |')))
        self.assertTrue(any("after" in w for w in model["graph_errors"]))
        self.assertNotIn("-->", visuals.diagram_sources(model)["dependencies"][1])

    def test_evidence_does_not_promote_planned_command_to_observation(self):
        model = visuals.project_model(source())
        item = model["evidence"][0]
        self.assertEqual(item["method"], "python check.py")
        self.assertEqual(item["evidence"], "")
        self.assertEqual(item["verdict"], "미검증")
        self.assertIn("증거 미검증", visuals.render_views(source(), model))

    def test_long_graph_retains_all_tasks_but_declines_unreadable_diagram(self):
        rows = "\n".join(f"| A{i} | 없음 |" for i in range(25))
        model = visuals.project_model(source("## 작업 분할\n| 작업 | after |\n|---|---|\n" + rows))
        self.assertEqual(len(model["tasks"]), 25)
        self.assertTrue(any("24개" in w for w in model["graph_errors"]))
        self.assertIn("A24", visuals.render_views(source(), model))

    def test_labels_cannot_inject_svg_or_mermaid_directives(self):
        hostile = '<script onclick="x"> & end [x]'
        model = visuals.project_model(source(PLAN.replace("승인 문서 위치", hostile)))
        html = visuals.render_views(source(), model)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script", html)
        encoded = visuals.mermaid_label('end\"\nclick x "https://example.com"')
        self.assertNotIn('"', encoded)
        self.assertNotIn("\n", encoded)


class Exports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = self.root / "Plan--A.md"
        self.plan.write_text(PLAN, encoding="utf-8")

    def run_cli(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / "dashboard.py"), "--repo", str(self.root),
                               "--plan", str(self.plan), "--output", "dashboard.html", "--diagram-dir", "diagrams", *extra],
                              capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))

    def test_offline_export_contains_valid_svg_sources_and_preserves_inputs(self):
        before = self.plan.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = list((self.root / "diagrams").iterdir())
        self.assertEqual(len(files), 4)
        for file in files:
            content = file.read_text(encoding="utf-8")
            self.assertIn(hashlib.sha256(before).hexdigest(), content)
            if file.suffix == ".svg":
                svg = ET.fromstring(content)
                self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertFalse(any(e.tag.endswith("script") or e.tag.endswith("foreignObject") for e in svg.iter()))
        html = (self.root / "dashboard.html").read_text(encoding="utf-8")
        HTMLParser().feed(html)
        self.assertIn("task-board", html)
        self.assertIn("<svg", html)
        self.assertNotIn("<script", html)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(self.run_cli("--overwrite").returncode, 0)

    def test_export_conflict_preflight_prevents_dashboard_write(self):
        folder = self.root / "diagrams"
        folder.mkdir()
        conflict = folder / "01-Plan--A-workflow.mmd"
        conflict.write_text("user diagram", encoding="utf-8")
        result = self.run_cli("--overwrite")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "dashboard.html").exists())
        self.assertEqual(conflict.read_text(), "user diagram")

    def test_export_cannot_escape_repository(self):
        sources = [dashboard.load_source(self.root, self.plan)]
        with self.assertRaises(ValueError):
            dashboard.prepare_exports(self.root, Path("../outside"), sources)


if __name__ == "__main__":
    unittest.main()
