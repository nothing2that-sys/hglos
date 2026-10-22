#!/usr/bin/env python3
"""병렬 구현 가능 여부를 판정한다. 모델이 눈으로 판정하지 않게 하는 도구다.

    python check_parallel.py <tasks.json> [--repo <저장소 루트>] [--json]

입력 (tasks.json):
    {
      "contracts_frozen": true,          # 여러 작업이 같이 쓰는 계약이 동결됐는가 (필수)
      "max_parallel": 3,                 # 동시 실행 상한 (선택, 기본 3)
      "min_minutes": 30,                 # 나눌 가치가 있는 작업 크기 하한 (선택, 기본 30)
      "tasks": [
        {"name": "A", "write": ["src/a/**", "docs/a.md"],
         "resources": ["cad-license"], "minutes": 60},
        {"name": "B", "write": ["src/b/**"], "after": ["A"]}   # after: 먼저 끝나야 하는 작업 (선택)
      ]
    }

출력은 실행 단계 목록이다. 단계는 위에서 아래로 순서대로 돌리고, 한 단계 안의 작업만 동시에 돈다.
선후 의존(after)이 있는 작업은 의존 대상보다 뒤 단계에 놓인다.

판정 규칙 (하나라도 걸리면 그 쌍은 직렬):
  1. 쓰기 범위가 겹친다      - 같은 파일에 닿을 수 있으면 겹침. 모호하면 겹침으로 본다
  2. 공유 자원이 겹친다      - git 에 안 보이는 것(장비, 라이선스 프로그램, 빌드 출력 폴더)
  3. 계약이 동결되지 않았다  - 전체가 "조건부"
  4. 작업이 하한보다 작다    - 그 작업은 직렬 권장(분할 비용이 이득보다 크다)
  5. after 로 묶인 작업      - 같은 단계에 놓지 않는다

종료 코드: 0 = 판정 완료 (결과는 출력), 2 = 입력 오류. 입력이 이상하면 판정하지 않는다.
표준 라이브러리만 쓴다. CWD 에 의존하지 않는다.
"""

import argparse
import json
import os
import posixpath
import subprocess
import re
import sys

WILDCARD = re.compile(r"[*?\[]")


class InputError(Exception):
    pass


def norm(pattern, repo=None):
    """경로 정규화. 같은 파일을 다른 철자로 적어 겹침을 놓치지 않게 한다.

    - 역슬래시 -> 슬래시, '//' '.' '..' 정리, 앞뒤 공백 제거
    - 대소문자 무시 (Windows 파일시스템 기준. 안전한 쪽)
    - 절대경로는 --repo 기준 상대경로로 바꾼다. --repo 가 없거나 저장소 밖이면 입력 오류
    - '.' 또는 '' (저장소 전체) 는 '**'
    """
    p = pattern.strip().replace("\\", "/")
    trailing = p.endswith("/")
    is_abs = p.startswith("/") or re.match(r"^[A-Za-z]:/", p) is not None
    if is_abs:
        if not repo:
            raise InputError(f"절대경로는 --repo 와 함께만 쓸 수 있다: {pattern}")
        root = os.path.abspath(repo).replace("\\", "/").rstrip("/")
        cand = p
        if cand.lower().startswith(root.lower() + "/"):
            p = cand[len(root) + 1:]
        elif cand.lower() == root.lower():
            p = "."
        else:
            raise InputError(f"저장소 밖 경로: {pattern}")
    p = posixpath.normpath(p) if p else "."
    if p.startswith("../") or p == "..":
        raise InputError(f"저장소 밖 경로: {pattern}")
    if p in (".", "/"):
        return "**"
    if trailing:
        p += "/"
    return p.lower()


def glob_to_regex(p):
    """'**' 는 디렉토리를 넘고 '*' '?' 는 넘지 않는다. '[...]' 는 문자 클래스. 끝이 '/' 면 그 아래 전부."""
    if p.endswith("/"):
        p += "**"
    out = []
    i = 0
    while i < len(p):
        c = p[i]
        if c == "*":
            if p[i:i + 2] == "**":
                i += 2
                if i < len(p) and p[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = p.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = p[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def static_prefix(pattern):
    """와일드카드 앞까지. 'src/a/**' -> 'src/a/'"""
    m = WILDCARD.search(pattern)
    return pattern if m is None else pattern[:m.start()]


SKIP_WALK = {".git", "bin", "obj", "packages", "node_modules", ".vs", "__pycache__"}


def list_files(repo):
    """저장소 파일 목록 (소문자). git 저장소면 git ls-files (추적 + 무시되지 않은 미추적).

    gitignore 대상(빌드 산출물·패키지)까지 훑으면 큰 저장소에서 수 분이 걸린다(실측: 120초 초과).
    git 이 없거나 실패하면 흔한 산출물 폴더를 건너뛰며 직접 훑는다.
    """
    try:
        out = subprocess.run(["git", "-C", repo, "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, timeout=60)
        if out.returncode == 0:
            return [x.decode("utf-8", "replace").replace("\\", "/").lower()
                    for x in out.stdout.split(b"\0") if x]
    except (OSError, subprocess.SubprocessError):
        pass
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_WALK]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), repo).replace("\\", "/")
            files.append(rel.lower())
    return files


def _is_literal(p):
    return WILDCARD.search(p) is None and not p.endswith("/")


def _under(path, base):
    """path 가 base 와 같거나 base 디렉토리 아래인가."""
    return path == base or path.startswith(base.rstrip("/") + "/")


def patterns_overlap(a, b, files):
    """정규화된 두 쓰기 패턴이 같은 파일에 닿을 수 있는가. 모호하면 True (안전한 쪽)."""
    ra, rb = glob_to_regex(a), glob_to_regex(b)
    la, lb = _is_literal(a), _is_literal(b)
    if la and lb:
        # 하나가 다른 하나의 상위 디렉토리일 수 있다 ('src/a' 와 'src/a/x.cs')
        return _under(a, b) or _under(b, a)
    if la or lb:
        lit, pat, rpat = (a, b, rb) if la else (b, a, ra)
        if rpat.match(lit):
            return True
        # 리터럴이 디렉토리일 수 있다: 패턴의 고정 접두가 그 아래에 있으면 겹침
        return _under(static_prefix(pat), lit) or static_prefix(pat).startswith(lit.rstrip("/") + "/")
    for f in files:
        if ra.match(f) and rb.match(f):
            return True
    sa, sb = static_prefix(a), static_prefix(b)
    return sa.startswith(sb) or sb.startswith(sa)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise InputError(f"입력 파일이 없다: {path}")
    except json.JSONDecodeError as e:
        raise InputError(f"JSON 이 아니다: {e}")
    if not isinstance(data, dict):
        raise InputError("최상위는 객체여야 한다")
    if "contracts_frozen" not in data or not isinstance(data["contracts_frozen"], bool):
        raise InputError("contracts_frozen (true/false) 가 필요하다. 모르면 false 로 적는다")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < 2:
        raise InputError("tasks 는 2개 이상의 목록이어야 한다")
    names = set()
    for t in tasks:
        if not isinstance(t, dict):
            raise InputError("tasks 항목은 객체여야 한다")
        name = t.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InputError("모든 작업에 name 이 필요하다")
        if name in names:
            raise InputError(f"작업 이름 중복: {name}")
        names.add(name)
        w = t.get("write")
        if not isinstance(w, list) or not w or not all(isinstance(x, str) and x.strip() for x in w):
            raise InputError(f"{name}: write 는 비어 있지 않은 문자열 목록이어야 한다 (쓰기 범위를 모르면 병렬 판정 불가)")
        r = t.get("resources", [])
        if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
            raise InputError(f"{name}: resources 는 문자열 목록이어야 한다")
        aft = t.get("after", [])
        if not isinstance(aft, list) or not all(isinstance(x, str) for x in aft):
            raise InputError(f"{name}: after 는 작업 이름 목록이어야 한다")
        m = t.get("minutes")
        if m is not None and (isinstance(m, bool) or not isinstance(m, (int, float)) or m <= 0):
            raise InputError(f"{name}: minutes 는 양수여야 한다")
    for t in tasks:
        for x in t.get("after", []):
            if x not in names:
                raise InputError(f"{t['name']}: after 에 없는 작업 이름: {x}")
            if x == t["name"]:
                raise InputError(f"{t['name']}: 자기 자신을 after 로 둘 수 없다")
    deps = {t["name"]: set(t.get("after", [])) for t in tasks}
    state = {}

    def visit(v, path):
        if state.get(v) == 1:
            raise InputError("after 순환: " + " -> ".join(path + [v]))
        if state.get(v) == 2:
            return
        state[v] = 1
        for w in deps[v]:
            visit(w, path + [v])
        state[v] = 2

    for v in deps:
        visit(v, [])
    mp = data.get("max_parallel", 3)
    mm = data.get("min_minutes", 30)
    for key, val in (("max_parallel", mp), ("min_minutes", mm)):
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise InputError(f"{key} 는 1 이상의 정수여야 한다")
    if mp < 2:
        raise InputError("max_parallel 이 2 미만이면 병렬 판정이 의미 없다")
    return data


def analyze(data, files):
    tasks = data["tasks"]
    min_minutes = data.get("min_minutes", 30)
    max_parallel = data.get("max_parallel", 3)
    n = len(tasks)
    conflicts = {i: {} for i in range(n)}  # i -> {j: [사유]}

    for i in range(n):
        for j in range(i + 1, n):
            reasons = []
            hits = sorted({f"{pa} ~ {pb}"
                           for pa, na in zip(tasks[i]["write"], tasks[i]["_norm"])
                           for pb, nb in zip(tasks[j]["write"], tasks[j]["_norm"])
                           if patterns_overlap(na, nb, files)})
            if hits:
                reasons.append("쓰기 겹침: " + "; ".join(hits))
            shared = sorted(set(tasks[i].get("resources", [])) & set(tasks[j].get("resources", [])))
            if shared:
                reasons.append("공유 자원: " + ", ".join(shared))
            if reasons:
                conflicts[i][j] = reasons
                conflicts[j][i] = reasons

    small = {i for i, t in enumerate(tasks) if t.get("minutes") is not None and t["minutes"] < min_minutes}

    # 단계 만들기: 의존이 끝난 작업 중에서 큰 것부터, 충돌 없는 것끼리 상한까지 한 단계에 넣는다.
    # 작은 작업은 혼자 한 단계를 차지한다(직렬 권장).
    idx = {t["name"]: i for i, t in enumerate(tasks)}
    deps = {i: {idx[x] for x in t.get("after", [])} for i, t in enumerate(tasks)}
    done, remaining, stages = set(), set(range(n)), []
    while remaining:
        ready = sorted((i for i in remaining if deps[i] <= done),
                       key=lambda i: (-(tasks[i].get("minutes") or 0), i))
        stage = []
        for i in ready:
            if i in small:
                continue
            if len(stage) < max_parallel and all(k not in conflicts[i] and k not in deps[i] for k in stage):
                stage.append(i)
        if not stage:
            stage = [ready[0]]
        stages.append(stage)
        done |= set(stage)
        remaining -= set(stage)
    parallel_groups = [g for g in stages if len(g) >= 2]
    serial = [g[0] for g in stages if len(g) == 1]

    rows = []
    for i, t in enumerate(tasks):
        if i in small:
            verdict = f"직렬 권장 (작업 {t['minutes']}분 < 하한 {min_minutes}분)"
        elif any(i in g for g in parallel_groups):
            verdict = "병렬 가능" if data["contracts_frozen"] else "조건부 (계약 동결 먼저)"
        else:
            verdict = "직렬"
        rows.append({
            "name": t["name"],
            "write": t["write"],
            "conflicts": {tasks[j]["name"]: r for j, r in conflicts[i].items()},
            "resources": t.get("resources", []),
            "verdict": verdict,
        })

    return {
        "contracts_frozen": data["contracts_frozen"],
        "max_parallel": max_parallel,
        "rows": rows,
        "stages": [[tasks[i]["name"] for i in g] for g in stages],
        "parallel_groups": [[tasks[i]["name"] for i in g] for g in parallel_groups],
        "serial": [tasks[i]["name"] for i in serial],
    }


def render(result):
    lines = ["| 작업 | 쓰는 범위 | 겹침 | 공유 자원 | 판정 |", "|---|---|---|---|---|"]
    for r in result["rows"]:
        conf = "; ".join(f"{k}: {' / '.join(v)}" for k, v in r["conflicts"].items()) or "-"
        lines.append(f"| {r['name']} | {', '.join(r['write'])} | {conf} | {', '.join(r['resources']) or '-'} | {r['verdict']} |")
    lines.append("")
    tag = "" if result["contracts_frozen"] else " (조건부: 공유 계약을 먼저 동결해야 한다)"
    lines.append("실행 단계 - 위에서 아래로 순서대로, 한 단계 안의 작업만 동시에 돈다:")
    for k, g in enumerate(result["stages"], 1):
        kind = "동시" if len(g) >= 2 else "단독"
        lines.append(f"단계 {k} [{kind}]: {', '.join(g)}{tag if len(g) >= 2 else ''}")
    if not result["parallel_groups"]:
        lines.append("동시 실행 단계 없음 - 전부 직렬")
    lines.append(f"동시 실행 상한: {result['max_parallel']}")
    lines.append("통합 후 검증 1회는 병렬 여부와 무관하게 필수다.")
    return "\n".join(lines)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="병렬 구현 가능 여부 판정")
    ap.add_argument("tasks_json")
    ap.add_argument("--repo", help="저장소 루트. 주면 실제 파일 목록으로 겹침을 본다")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    args = ap.parse_args(argv)
    try:
        data = load(args.tasks_json)
        for t in data["tasks"]:
            t["_norm"] = [norm(x, args.repo) for x in t["write"]]
        files = []
        if args.repo:
            if not os.path.isdir(args.repo):
                raise InputError(f"저장소 경로가 없다: {args.repo}")
            files = list_files(args.repo)
        result = analyze(data, files)
    except InputError as e:
        print(f"입력 오류 - 판정하지 않는다: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
