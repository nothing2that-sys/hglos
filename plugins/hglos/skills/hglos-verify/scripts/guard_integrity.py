#!/usr/bin/env python3
"""회차 실행 전후로 저장소가 변경되지 않았음을 기계적으로 검증한다.

왜 필요한가:
`--sandbox read-only` 는 프로세스 수준 강제로 설계됐지만, 실측에서
`codex-windows-sandbox-setup.exe` 가 없으면 codex 가 다른 경로로 폴백해
읽기 전용 보장이 걸리지 않는 것이 확인됐다. 실패는 조용했다 - 리뷰는
정상 완료되고 샌드박스만 빠졌다.

플래그 하나에 T0 등급을 걸어두면 그 플래그가 조용히 무력화될 때 알 수 없다.
그래서 git 을 오라클로 써서 직접 검증한다. 샌드박스가 되든 안 되든 작동한다.

사용:
  python guard_integrity.py snapshot <repo> --out <경로>
  python guard_integrity.py verify   <repo> --baseline <경로>
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def git(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout


def fingerprint(repo, exclude_dirs=("validation/", "validation\\")):
    """작업트리 상태의 지문.

    diff = 추적 파일의 내용 변경
    porcelain = 신규/삭제/스테이징 상태
    둘을 합치면 작업트리의 모든 변화가 잡힌다.

    `validation/` 은 제외한다. 이 스킬의 산출물이 들어가는 곳이라
    매 회차 바뀌는 것이 정상이다. 검증 대상은 소스다.
    """
    # diff 는 추적 파일만 보므로 validation/ 이 gitignore 되어 있으면 애초에 안 들어온다.
    # 혹시 추적되고 있어도 pathspec 으로 배제한다.
    diff = git(repo, "--no-pager", "diff", "--", ".", ":(exclude)validation")
    porcelain = git(repo, "status", "--porcelain")
    head = git(repo, "rev-parse", "HEAD").strip()

    def relevant(line):
        payload = line[3:].strip().strip('"')
        return not any(payload.startswith(d) for d in exclude_dirs)

    status_lines = [l for l in porcelain.strip().splitlines() if l and relevant(l)]

    def h(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return {
        "head": head,
        "diff_sha256": h(diff),
        "status_sha256": h("\n".join(status_lines)),
        "diff_lines": len(diff.splitlines()),
        "diff_bytes": len(diff.encode("utf-8")),
        "status": status_lines,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "verify"])
    ap.add_argument("repo")
    ap.add_argument("--out", help="snapshot 저장 경로")
    ap.add_argument("--baseline", help="verify 시 비교할 snapshot 경로")
    args = ap.parse_args()

    repo = Path(args.repo)
    try:
        fp = fingerprint(repo)
    except RuntimeError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2

    if args.mode == "snapshot":
        out = Path(args.out or (repo / "validation" / "integrity.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fp, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print("무결성 기준선 기록")
        print(f"  HEAD        : {fp['head'][:12]}")
        print(f"  diff sha256 : {fp['diff_sha256'][:16]}  ({fp['diff_lines']}행)")
        print(f"  변경 파일   : {len(fp['status'])}건")
        for line in fp["status"]:
            print(f"    {line}")
        print(f"  저장: {out}")
        return 0

    # verify
    base_path = Path(args.baseline or (repo / "validation" / "integrity.json"))
    if not base_path.exists():
        print(f"[오류] 기준선 없음: {base_path}", file=sys.stderr)
        print("먼저 snapshot 을 실행해야 한다.", file=sys.stderr)
        return 2

    base = json.loads(base_path.read_text(encoding="utf-8"))

    problems = []
    if base["head"] != fp["head"]:
        problems.append(f"HEAD 변경: {base['head'][:12]} -> {fp['head'][:12]}")
    if base["diff_sha256"] != fp["diff_sha256"]:
        problems.append(
            "작업트리 내용 변경: diff sha256 "
            f"{base['diff_sha256'][:16]} -> {fp['diff_sha256'][:16]} "
            f"({base['diff_lines']}행/{base.get('diff_bytes','?')}B -> "
            f"{fp['diff_lines']}행/{fp['diff_bytes']}B)"
        )
    if base["status_sha256"] != fp["status_sha256"]:
        added = set(fp["status"]) - set(base["status"])
        removed = set(base["status"]) - set(fp["status"])
        if added:
            problems.append(f"신규 변경 항목: {sorted(added)}")
        if removed:
            problems.append(f"사라진 변경 항목: {sorted(removed)}")

    if not problems:
        print("무결성 검증 통과 - 소스 변경 없음")
        print(f"  diff sha256 : {fp['diff_sha256'][:16]}  ({fp['diff_lines']}행)")
        return 0

    print("[실패] 소스가 변경됐다. T0 위반.")
    for p in problems:
        print(f"  - {p}")
    print("\n조치:")
    print("  1. 즉시 중단. 추가 회차를 돌리지 않는다.")
    print("  2. 누가 고쳤는지 확인 (codex 폴백 / 에이전트 자체 수정).")
    print("  3. codex doctor 로 샌드박스 설치 상태 점검.")
    print("  4. 변경을 되돌린 뒤 재실행. 이번 회차 결과는 신뢰하지 않는다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
