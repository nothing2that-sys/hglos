"""shared/scripts 공용 판정 스크립트 테스트.

각 스크립트를 자식 프로세스로 돌려 종료 코드와 출력을 본다. 자식에게 UTF-8 을 강제한다
(강제하지 않으면 한글이 깨져 assertNotIn 이 무조건 통과했다).
실패 경로(입력 오류 -> 종료 코드 2)를 반드시 같이 본다.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("HGLOS_SHARED_SCRIPTS", REPO / "shared" / "scripts"))


def run(script, *args, stdin=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                       input=stdin, capture_output=True, text=True, encoding="utf-8", env=env)
    return p.returncode, p.stdout, p.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def tasks(self, data):
        return self.write("tasks.json", json.dumps(data, ensure_ascii=False))


class CheckParallel(Base):
    def base(self, **kw):
        d = {"contracts_frozen": True, "tasks": [
            {"name": "A", "write": ["src/a/**"], "minutes": 60},
            {"name": "B", "write": ["src/b/**"], "minutes": 60},
        ]}
        d.update(kw)
        return d

    def test_disjoint_is_parallel(self):
        code, out, _ = run("check_parallel.py", self.tasks(self.base()))
        self.assertEqual(code, 0)
        self.assertIn("단계 1 [동시]: A, B", out)
        self.assertNotIn("[단독]", out)

    def test_overlap_wildcard_vs_literal(self):
        d = self.base()
        d["tasks"][1]["write"] = ["src/a/Foo.cs"]
        code, out, _ = run("check_parallel.py", self.tasks(d))
        self.assertEqual(code, 0)
        self.assertIn("쓰기 겹침", out)
        self.assertIn("동시 실행 단계 없음", out)

    def test_overlap_by_prefix_when_no_files(self):
        d = self.base()
        d["tasks"][1]["write"] = ["src/**/*.cs"]
        code, out, _ = run("check_parallel.py", self.tasks(d))
        self.assertIn("쓰기 겹침", out)

    def test_overlap_found_via_real_files(self):
        self.write("repo/lib/x/util.py", "")
        d = self.base()
        d["tasks"][0]["write"] = ["lib/*/util.py"]
        d["tasks"][1]["write"] = ["lib/x/*.py"]
        code, out, _ = run("check_parallel.py", self.tasks(d), "--repo", self.tmp / "repo")
        self.assertEqual(code, 0)
        self.assertIn("쓰기 겹침", out)

    def test_shared_resource_blocks(self):
        d = self.base()
        d["tasks"][0]["resources"] = ["cad"]
        d["tasks"][1]["resources"] = ["cad"]
        code, out, _ = run("check_parallel.py", self.tasks(d))
        self.assertIn("공유 자원: cad", out)
        self.assertIn("동시 실행 단계 없음", out)

    def test_contracts_not_frozen_is_conditional(self):
        code, out, _ = run("check_parallel.py", self.tasks(self.base(contracts_frozen=False)))
        self.assertEqual(code, 0)
        self.assertIn("조건부", out)

    def test_small_task_serial(self):
        d = self.base()
        d["tasks"][1]["minutes"] = 10
        code, out, _ = run("check_parallel.py", self.tasks(d))
        self.assertIn("직렬 권장", out)
        self.assertIn("동시 실행 단계 없음", out)

    def test_max_parallel_caps_group(self):
        d = {"contracts_frozen": True, "max_parallel": 2, "tasks": [
            {"name": n, "write": [f"{n}/**"], "minutes": 60} for n in "ABC"]}
        code, out, _ = run("check_parallel.py", self.tasks(d), "--json")
        res = json.loads(out)
        self.assertTrue(all(len(g) <= 2 for g in res["parallel_groups"]))
        self.assertEqual(sum(len(g) for g in res["parallel_groups"]) + len(res["serial"]), 3)

    def test_same_file_different_spelling_not_parallel(self):
        # 재현된 오판: 철자만 다른 같은 파일을 병렬로 판정했다
        cases = [("Src/A.cs", "src/a.cs"), ("src\\a.cs", "src/a.cs"), ("src/./a.cs", "src/a.cs"),
                 ("src//a.cs", "src/a.cs"), ("src/../src/a.cs", "src/a.cs"), ("src/[ab].cs", "src/a.cs"),
                 (".", "src/a.cs"), ("src/a", "src/a/**")]
        for a, b in cases:
            d = self.base(tasks=[{"name": "A", "write": [a], "minutes": 60},
                                 {"name": "B", "write": [b], "minutes": 60}])
            code, out, err = run("check_parallel.py", self.tasks(d))
            self.assertEqual(code, 0, (a, b, err))
            self.assertIn("동시 실행 단계 없음", out, (a, b))

    def test_absolute_path_needs_repo(self):
        d = self.base(tasks=[{"name": "A", "write": ["C:/x/a.cs"], "minutes": 60},
                             {"name": "B", "write": ["b.cs"], "minutes": 60}])
        code, _, err = run("check_parallel.py", self.tasks(d))
        self.assertEqual(code, 2)

    def test_after_puts_dependent_in_later_stage(self):
        # 실사용에서 발견: 충돌이 없어도 B 가 A 의 산출물을 쓰면 같은 단계에 두면 안 된다
        d = self.base()
        d["tasks"][1]["after"] = ["A"]
        code, out, _ = run("check_parallel.py", self.tasks(d), "--json")
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["stages"], [["A"], ["B"]])

    def test_after_chain_with_parallel_rest(self):
        d = {"contracts_frozen": True, "tasks": [
            {"name": "A", "write": ["a/**"], "minutes": 30},
            {"name": "B", "write": ["b/**"], "minutes": 60},
            {"name": "C", "write": ["c/**"], "minutes": 60, "after": ["A"]},
        ]}
        res = json.loads(run("check_parallel.py", self.tasks(d), "--json")[1])
        self.assertEqual([sorted(g) for g in res["stages"]], [["A", "B"], ["C"]])

    def test_after_errors(self):
        for tasks in (
            [{"name": "A", "write": ["a"], "after": ["X"]}, {"name": "B", "write": ["b"]}],
            [{"name": "A", "write": ["a"], "after": ["A"]}, {"name": "B", "write": ["b"]}],
            [{"name": "A", "write": ["a"], "after": ["B"]}, {"name": "B", "write": ["b"], "after": ["A"]}],
            [{"name": "A", "write": ["a"], "after": "B"}, {"name": "B", "write": ["b"]}],
        ):
            code, _, err = run("check_parallel.py", self.tasks(self.base(tasks=tasks)))
            self.assertEqual(code, 2, tasks)

    def test_repo_listing_skips_ignored_build_output(self):
        repo = self.tmp / "g"
        (repo / "bin").mkdir(parents=True)
        (repo / "bin" / "shared.cs").write_text("", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "x.cs").write_text("", encoding="utf-8")
        (repo / ".gitignore").write_text("bin/\n", encoding="utf-8")
        if subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode != 0:
            self.skipTest("git 없음")
        d = self.base(tasks=[{"name": "A", "write": ["*/x.cs"], "minutes": 60},
                             {"name": "B", "write": ["src/*.cs"], "minutes": 60}])
        code, out, _ = run("check_parallel.py", self.tasks(d), "--repo", repo)
        self.assertEqual(code, 0)
        self.assertIn("쓰기 겹침", out)  # 추적되지 않았지만 무시되지 않은 파일은 목록에 든다

    def test_input_errors_fail_closed(self):
        bad = [
            {"tasks": [{"name": "A", "write": ["a"]}, {"name": "B", "write": ["b"]}]},  # contracts_frozen 없음
            self.base(tasks=[{"name": "A", "write": ["a"]}]),                          # 작업 1개
            self.base(tasks=[{"name": "A", "write": []}, {"name": "B", "write": ["b"]}]),  # write 비어 있음
            self.base(tasks=[{"name": "A", "write": ["a"]}, {"name": "A", "write": ["b"]}]),  # 이름 중복
            self.base(max_parallel=True),
        ]
        for d in bad:
            code, out, err = run("check_parallel.py", self.tasks(d))
            self.assertEqual(code, 2, d)
            self.assertIn("판정하지 않는다", err)
            self.assertEqual(out, "")
        code, _, err = run("check_parallel.py", self.tmp / "nope.json")
        self.assertEqual(code, 2)


class StaleIds(Base):
    LEDGER = "| ID | 무엇 | 상태 |\n|---|---|---|\n| CN-01 | a | CLOSED |\n| CN-02 | b | OPEN |\n| CN-03 | c | 폐기 |\n"

    def test_finds_closed_id_in_pending_context(self):
        led = self.write("doc/CONCERNS.md", self.LEDGER)
        self.write("doc/handoff.md", "남은 항목: CN-01 확인 대기\nCN-02 대기\nCN-03 은 닫혔다\n")
        code, out, _ = run("stale_ids.py", "--ledger", led, self.tmp / "doc")
        self.assertEqual(code, 1)
        self.assertIn("handoff.md:1", out)
        self.assertNotIn("handoff.md:2", out)  # CN-02 는 열린 항목
        self.assertNotIn("handoff.md:3", out)  # 대기 표현 없음
        self.assertNotIn("CONCERNS.md", out.split("\n", 1)[1])  # 대장 자체는 제외

    def test_status_with_annotation(self):
        led = self.write("doc/L.md", "| ID | 상태 |\n|---|---|\n| OI-1 | **CLOSED (2026-10-02)** |\n| OI-2 | CLOSED 2026-10-13 |\n| OI-3 | OPEN (일부 해결) |\n")
        self.write("doc/x.md", "OI-1 대기\nOI-2 남은 것\nOI-3 대기\n")
        code, out, _ = run("stale_ids.py", "--ledger", led, self.tmp / "doc")
        self.assertEqual(code, 1)
        self.assertIn("닫힌 ID 2개", out)
        self.assertIn("x.md:1", out)
        self.assertIn("x.md:2", out)
        self.assertNotIn("x.md:3", out)

    def test_korean_particle_after_id(self):
        self.write("d/x.md", "CN-01은 아직 대기 중이다\n")
        code, out, _ = run("stale_ids.py", "--ids", "CN-01", self.tmp / "d")
        self.assertEqual(code, 1)

    def test_status_variants_and_link_id(self):
        led = self.write("doc/L.md", "| ID | 상태 |\n|---|---|\n| [A-1](#a-1) | ✅ 완료 |\n| A-2 | 완료됨 |\n| A-3 | Closed-2026-10-02 |\n| A-4 | 검토중 |\n")
        self.write("doc/x.md", "A-1 대기\nA-2 대기\nA-3 대기\n")
        code, out, _ = run("stale_ids.py", "--ledger", led, self.tmp / "doc")
        self.assertEqual(code, 1)
        self.assertIn("닫힌 ID 3개", out)
        self.assertIn("열린 것으로 취급한 상태값", out)

    def test_clean(self):
        led = self.write("doc/CONCERNS.md", self.LEDGER)
        self.write("doc/x.md", "CN-01 은 닫혔다\n")
        code, out, _ = run("stale_ids.py", "--ledger", led, self.tmp / "doc")
        self.assertEqual(code, 0)
        self.assertIn("발견 없음", out)

    def test_unchecked_checkbox_is_pending(self):
        # 회귀 픽스처: 실사고 형태 (닫힌 M1 을 미완료 체크박스가 들고 있음)
        self.write("d/x.md", "- [ ] **headless NRE 조사** - M1 합격 기준 회복\n"
                             "- [x] **headless NRE 조사 - 항목 내림** M1 달성\n"
                             "* [ ] M1 재확인\n"
                             "설명 중 [ ] 표기와 M1\n")
        code, out, _ = run("stale_ids.py", "--ids", "M1", self.tmp / "d")
        self.assertEqual(code, 1)
        self.assertIn("x.md:1", out)
        self.assertNotIn("x.md:2", out)  # 체크된 항목
        self.assertIn("x.md:3", out)
        self.assertNotIn("x.md:4", out)  # 줄머리 체크박스가 아님

    def test_checkbox_forms_and_words_branch(self):
        lines = ["+ [ ] M1 a", "   - [ ] M1 b", "\t* [ ] M1 c", "1. [ ] M1 d", "> - [ ] M1 e",
                 "- [X] M1 f", "-[ ] M1 g", "- [  ] M1 h"]
        self.write("d/x.md", "\n".join(lines) + "\n")
        code, out, _ = run("stale_ids.py", "--ids", "M1", self.tmp / "d")
        self.assertEqual(code, 1)
        found = {int(s.split("x.md:")[1].split()[0]) for s in out.splitlines() if "x.md:" in s}
        self.assertEqual(found, {1, 2, 3, 4, 5})
        # --words 에 체크박스를 빼면 검사하지 않는다
        code, out, _ = run("stale_ids.py", "--ids", "M1", "--words", "TODO", self.tmp / "d")
        self.assertEqual(code, 0)
        # --words 에 넣으면 켜진다
        code, out, _ = run("stale_ids.py", "--ids", "M1", "--words", "TODO,- [ ]", self.tmp / "d")
        self.assertEqual(code, 1)

    def test_id_boundary(self):
        self.write("d/x.md", "CN-011 대기\n")
        code, out, _ = run("stale_ids.py", "--ids", "CN-01", self.tmp / "d")
        self.assertEqual(code, 0)

    def test_no_closed_ids_is_error_not_pass(self):
        led = self.write("doc/L.md", "| ID | 상태 |\n|---|---|\n| X-1 | OPEN |\n")
        code, out, err = run("stale_ids.py", "--ledger", led, self.tmp / "doc")
        self.assertEqual(code, 2)
        self.assertIn("판정하지 않는다", err)

    def test_missing_target_is_error(self):
        code, _, err = run("stale_ids.py", "--ids", "A-1", self.tmp / "nothing")
        self.assertEqual(code, 2)


class SetDiff(Base):
    def test_equal(self):
        a = self.write("a.txt", "x\ny\n# 주석\n\n")
        b = self.write("b.txt", "y\nx\n")
        code, out, _ = run("set_diff.py", "--comments", a, b)
        self.assertEqual(code, 0)
        self.assertIn("판정: 같다", out)

    def test_same_count_different_names(self):
        a = self.write("a.txt", "x\ny\n")
        b = self.write("b.txt", "x\nz\n")
        code, out, _ = run("set_diff.py", a, b)
        self.assertEqual(code, 1)
        self.assertIn("기대에만 있음 1: y", out)
        self.assertIn("실제에만 있음 1: z", out)

    def test_duplicates_reported(self):
        a = self.write("a.txt", "x\ny\n")
        code, out, _ = run("set_diff.py", a, "-", stdin="x\ny\ny\n")
        self.assertEqual(code, 1)
        self.assertIn("중복", out)

    def test_hash_items_not_dropped_by_default(self):
        a = self.write("a.txt", "A\n#12\n")
        b = self.write("b.txt", "A\n")
        code, out, _ = run("set_diff.py", a, b)
        self.assertEqual(code, 1)
        self.assertEqual(run("set_diff.py", "--comments", a, b)[0], 0)

    def test_bom_on_stdin(self):
        a = self.write("a.txt", "A\nB\n")
        code, _, _ = run("set_diff.py", a, "-", stdin="\ufeffA\nB\n")
        self.assertEqual(code, 0)

    def test_both_empty_is_error(self):
        z = self.write("z.txt", "")
        self.assertEqual(run("set_diff.py", z, z)[0], 2)

    def test_input_errors(self):
        a = self.write("a.txt", "x\n")
        self.assertEqual(run("set_diff.py", a)[0], 2)
        self.assertEqual(run("set_diff.py", a, self.tmp / "nope.txt")[0], 2)
        self.assertEqual(run("set_diff.py", "-", "-")[0], 2)


if __name__ == "__main__":
    unittest.main()


class BuildSync(unittest.TestCase):
    def test_skill_copies_match_shared(self):
        p = subprocess.run([sys.executable, str(REPO / "tools" / "build.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(p.returncode, 0, p.stderr)
