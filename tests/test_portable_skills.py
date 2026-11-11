"""Portable installation, package fidelity, safe dashboard output and reviewer failures."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import install_skills
import package_skill

DASH = REPO / "plugin/hglos/skills/hglos-dashboard/scripts/dashboard.py"
VALIDATE = REPO / "plugin/hglos/skills/hglos-verify/scripts/validate_findings.py"
sys.path.insert(0, str(DASH.parent))
spec = importlib.util.spec_from_file_location("dashboard", DASH)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))


class PortableSkills(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fresh_install_is_self_contained_and_idempotent(self):
        dest = self.root / "skills"
        self.assertEqual(len(install_skills.install(dest)), len(package_skill.SKILLS))
        for skill in package_skill.SKILLS:
            for arc, src in package_skill.entries(skill):
                self.assertEqual((dest / arc).read_bytes(), src.read_bytes())
        self.assertEqual(install_skills.install(dest), [])
        result = run(dest / "hglos-design/scripts/check_parallel.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(dest / "hglos-dashboard/scripts/dashboard.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(dest / "hglos-coordinate/scripts/coordination.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(dest / "hglos-coordinate/scripts/setup_coordination.py", "--repo", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / '.hglos').exists())

    def test_conflict_late_in_batch_prevents_all_installation(self):
        target = self.root / "hglos-dashboard"
        target.mkdir()
        (target / "SKILL.md").write_text("local edits", encoding="utf-8")
        with self.assertRaises(ValueError):
            install_skills.install(self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["hglos-dashboard"])
        self.assertEqual((target / "SKILL.md").read_text(), "local edits")

    def test_dry_run_creates_nothing(self):
        dest = self.root / "absent"
        self.assertEqual(len(install_skills.install(dest, dry_run=True)), len(package_skill.SKILLS))
        self.assertFalse(dest.exists())

    def test_extra_file_conflicts(self):
        install_skills.install(self.root)
        (self.root / "hglos-design/notes.txt").write_text("mine")
        with self.assertRaises(ValueError):
            install_skills.install(self.root)

    def test_project_locations_share_agents_skills(self):
        for target in ("codex", "antigravity", "antigravity-cli"):
            self.assertEqual(install_skills.destination(target, project=self.root), self.root / ".agents/skills")
        self.assertEqual(install_skills.destination("claude", project=self.root), self.root / ".claude/skills")
        self.assertNotEqual(install_skills.destination("antigravity", user=True), install_skills.destination("antigravity-cli", user=True))

    def test_cli_install_shared_project(self):
        script = REPO / "tools/install_skills.py"
        first = run(script, "--target", "codex", "--project", self.root)
        second = run(script, "--target", "antigravity", "--project", self.root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"New skills: {len(package_skill.SKILLS)}", first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("New skills: 0", second.stdout)
        self.assertEqual(len(list((self.root / ".agents/skills").iterdir())), len(package_skill.SKILLS))

    def test_junction_parent_refused(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
        if result.returncode and b"Access is denied" in result.stderr:
            self.skipTest("Sandbox denies junction creation; real junction test not executed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.assertRaises(ValueError):
            install_skills.install(link / "skills")
        self.assertEqual(list(real.iterdir()), [])

    def test_archives_are_deterministic_and_complete(self):
        items = sum((package_skill.entries(s) for s in package_skill.SKILLS), [])
        a = package_skill.build("all", items, self.root / "a")
        b = package_skill.build("all", items, self.root / "b")
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(package_skill.verify(a, items), [])
        with zipfile.ZipFile(a) as archive:
            self.assertEqual(set(archive.namelist()), {arc for arc, _ in items})


PLAN = """# Slice <script>alert('x')</script>
**승인**: 범위 <이름> · 계획 <미기입>
## Current State
- 현재 위치: 구현 대기
- 다음 한 단계: 독립 검토
## 2. 종결 조건
| 조건 | 판정 |
|---|---|
| 실제 실행 | 미검증 |
## 7. 미결
- 장비 연결 미확인
## 8. 작업 분할
| 작업 | after |
|---|---|
| A | 없음 |
"""


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = self.root / "계획서.md"
        self.plan.write_text(PLAN, encoding="utf-8")

    def invoke(self, *extra, output="dashboard.html"):
        return run(DASH, "--repo", self.root, "--plan", self.plan, "--output", output, *extra)

    def test_html_escapes_source_retains_unknown_and_source_hash(self):
        before = self.plan.read_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        html = (self.root / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;미기입&gt;", html)
        self.assertIn(hashlib.sha256(before).hexdigest(), html)
        self.assertIn("L3", html)
        self.assertIn("미검증", html)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_output_does_not_replace_source_even_with_overwrite(self):
        result = self.invoke("--overwrite", output=str(self.plan))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.plan.read_text(encoding="utf-8"), PLAN)

    def test_existing_unrelated_document_refused(self):
        (self.root / "dashboard.md").write_text("user document", encoding="utf-8")
        result = self.invoke("--overwrite", output="dashboard.md")
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / "dashboard.md").read_text(), "user document")

    def test_marker_quoted_in_guide_does_not_authorize_overwrite(self):
        guide = "# Guide\nExample: HGLOS / PROJECT SNAPSHOT\n" + dashboard.SIGNATURE + "quoted\n" + dashboard.END_SIGNATURE
        (self.root / "guide.md").write_text(guide, encoding="utf-8")
        result = self.invoke("--overwrite", output="guide.md")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual((self.root / "guide.md").read_text(encoding="utf-8"), guide)

    def test_regeneration_requires_explicit_flag(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.invoke("--overwrite").returncode, 0)

    def test_markdown_and_missing_sections(self):
        self.plan.write_text("# Empty plan\n", encoding="utf-8")
        result = self.invoke(output="dashboard.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "dashboard.md").read_text(encoding="utf-8").count("미검증 · 절 없음"), 4)

    def test_source_and_output_escape_repository_refused(self):
        result = self.invoke(output="../outside.html")
        self.assertEqual(result.returncode, 2)
        outside = self.root.parent / (self.root.name + "-outside.md")
        outside.write_text(PLAN, encoding="utf-8")
        self.addCleanup(outside.unlink)
        with self.assertRaises(ValueError):
            dashboard.load_source(self.root, outside)

    def test_fenced_example_is_not_actual_state_or_concern(self):
        lines = ["```md", "## Current State", "| ID | 상태 |", "|---|---|", "| fake | CLOSED |", "```"]
        self.assertEqual(dashboard.sections(lines), [])
        self.assertEqual(dashboard.concern_counts(lines), {})

    def test_concerns_count_status_column_not_heading_or_history(self):
        lines = ["## 열린 항목", "| ID | 상태 |", "|---|---|", "| A | CLOSED |", "| B | OPEN |", "| C | |",
                 "", "| 날짜 | 결정 |", "|---|---|", "| 1 | CLOSED |"]
        self.assertEqual(dashboard.concern_counts(lines), {"CLOSED": 1, "OPEN": 1, "미해석(빈 상태)": 1})


class Findings(unittest.TestCase):
    def test_flat_tools_copy_supports_explicit_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            script = folder / "validate_findings.py"
            script.write_bytes(VALIDATE.read_bytes())
            schema = folder / "findings-schema.json"
            schema.write_bytes((VALIDATE.parent.parent / "assets/findings-schema.json").read_bytes())
            raw = folder / "raw.json"
            raw.write_text('{"findings": []}', encoding="utf-8")
            result = run(script, raw, "--schema", schema)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_empty_and_invalid_outputs_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "review.json"
            finding = {"path": "a.cs", "line": 1, "category": "logic", "severity": "high",
                       "evidence": "observed", "claim": "Defect", "detail": "Why"}
            for value, expected in [({"findings": []}, 0), ({"findings": [finding]}, 0),
                                    ({}, 2), ({"findings": None}, 2), ({"findings": [3]}, 2),
                                    ({"findings": [{**finding, "line": True}]}, 2),
                                    ({"findings": [{**finding, "line": 0}]}, 2),
                                    ({"findings": [{**finding, "severity": "pass"}]}, 2)]:
                with self.subTest(value=value):
                    raw.write_text(json.dumps(value), encoding="utf-8")
                    result = run(VALIDATE, raw)
                    self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
