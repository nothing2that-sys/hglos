#!/usr/bin/env python3
"""회차별 발견을 비교해 정지 조건을 판정한다.

비교 키는 `path:line` 이다. `category` 를 비교에 쓰지 않는다.
실측에서 같은 결함이 회차마다 logic/bounds 로 오갔고
(analyzer.cpp:29), 그 때문에 지문이 갈라져 수렴 판정이 늦어졌다.
라인 번호는 3회 모두 안정했다.

멀티셋으로 비교하므로 같은 라인의 서로 다른 결함 2건은 개수로 보존된다.
category/severity/evidence 변동은 별도로 '드리프트'로 보고한다.

컨텍스트 절약이 목적이다. 메인 세션은 round_*.md 전문을 읽지 않고
이 스크립트의 출력만 본다.
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# round_<정수>.json 만 회차 파일로 인정한다.
# codex_1.raw.json 같은 부산물이 걸리는 것을 막는다.
ROUND_RE = re.compile(r"^round_(\d+)\.json$")

LOW_SEVERITIES = {"low", "style"}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "style"]


def comparison_key(f):
    """비교 키. category 를 포함하지 않는다."""
    path = f.get("path")
    line = f.get("line")
    if path is not None:
        ln = str(line) if isinstance(line, int) else "-"
        return f"{str(path).replace(chr(92), '/')}:{ln}"
    # 구형 round 파일 폴백: fp 에서 마지막 세그먼트(category) 를 떼낸다
    fp = f.get("fp", "")
    parts = fp.rsplit(":", 1)
    return parts[0] if len(parts) == 2 else fp


def load_rounds(vdir):
    candidates = []
    for p in vdir.iterdir():
        m = ROUND_RE.match(p.name)
        if m:
            candidates.append((int(m.group(1)), p))
    candidates.sort()

    rounds = []
    for num, p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[오류] {p.name} 읽기 실패: {e}", file=sys.stderr)
            continue
        if "codex_invoked" not in data:
            print(f"[경고] {p.name} 에 codex_invoked 키가 없음. 건너뜀.",
                  file=sys.stderr)
            continue
        data["_file"] = p.name
        data.setdefault("round", num)
        rounds.append(data)
    rounds.sort(key=lambda d: d["round"])
    return rounds


def max_severity(findings):
    for s in SEVERITY_ORDER:
        if any(f.get("severity") == s for f in findings):
            return s
    return "none"


def report_drift(ok):
    """같은 위치에서 category/severity/evidence 가 회차마다 달라진 것을 찾는다.

    이건 코드에 대한 발견이 아니라 도구에 대한 관측이다.
    """
    seen = collections.defaultdict(lambda: collections.defaultdict(set))
    # 라인당 결함 수의 최대값. 한 라인에 결함 2개면 category 2종은 정상이다.
    max_per_round = collections.defaultdict(int)

    for r in ok:
        n = r["round"]
        per_key = collections.Counter(comparison_key(f) for f in r.get("findings", []))
        for k, c in per_key.items():
            max_per_round[k] = max(max_per_round[k], c)
        for f in r.get("findings", []):
            k = comparison_key(f)
            seen[k]["category"].add(f.get("category"))
            seen[k]["severity"].add(f.get("severity"))
            seen[k]["evidence"].add(f.get("evidence"))
            seen[k]["_rounds"].add(n)

    drifts = []
    for k, d in sorted(seen.items()):
        if len(d["_rounds"]) < 2:
            continue
        allowed = max_per_round[k]  # 이 위치의 정상적인 분류 종류 수
        for field in ("category", "severity", "evidence"):
            vals = {v for v in d[field] if v is not None}
            if len(vals) > allowed:
                drifts.append((k, field, sorted(vals), allowed))
    return drifts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("validation_dir")
    ap.add_argument("--cap", type=int, default=3)
    ap.add_argument("--show-drift", action="store_true", default=True)
    args = ap.parse_args()

    vdir = Path(args.validation_dir)
    if not vdir.is_dir():
        print(f"[오류] 디렉토리 없음: {vdir}", file=sys.stderr)
        return 2

    rounds = load_rounds(vdir)
    if not rounds:
        print(f"[오류] {vdir} 에 round_<N>.json 이 없음", file=sys.stderr)
        return 2

    ok = [r for r in rounds if r.get("codex_invoked") is True]
    failed = [r for r in rounds if r.get("codex_invoked") is not True]

    print("=" * 60)
    print("적대적 검증 - 정지 판정")
    print("=" * 60)
    print(f"성공 회차: {len(ok)}/{args.cap}    실패 회차: {len(failed)}")
    if failed:
        names = ", ".join(str(r["round"]) for r in failed)
        print(f"  ! 실패 회차 있음 (round {names}) - 최종 보고에 반드시 명시")

    if not ok:
        print("\n판정: 판정불가 - 성공한 회차가 없음")
        print("조치: Codex 호출을 먼저 해결. codex doctor / codex login 확인.")
        return 1

    # 멀티셋 비교
    msets = [collections.Counter(comparison_key(f) for f in r.get("findings", []))
             for r in ok]

    print("\n회차별 (비교 키 = path:line, category 제외)")
    cum = collections.Counter()
    for r, ms in zip(ok, msets):
        new_keys = {k for k in ms if k not in cum}
        cum.update({k: v for k, v in ms.items() if k not in cum})
        sev = max_severity(r.get("findings", []))
        total = sum(ms.values())
        print(f"  R{r['round']}: 발견 {total:2d}  고유위치 {len(ms):2d}  "
              f"신규위치 {len(new_keys):2d}  최고심각도 {sev}")

    all_keys = set()
    for ms in msets:
        all_keys |= set(ms)
    print(f"\n총 고유 위치: {len(all_keys)}")

    # --- 정지 조건 ---
    verdict = None
    detail = ""

    for i in range(1, len(msets)):
        if msets[i] == msets[i - 1]:
            if sum(msets[i].values()) == 0:
                verdict = "수렴 정지 (발견 없음)"
                detail = "연속 2회 발견 0건."
            else:
                verdict = "수렴 정지"
                detail = (f"R{ok[i-1]['round']} 와 R{ok[i]['round']} 의 발견 위치가 "
                          "개수까지 동일. 독립 시행이 같은 결과로 모였음.")
            break

    if verdict is None and len(ok) >= 2:
        prev = set()
        for ms in msets[:-1]:
            prev |= set(ms)
        new_keys = set(msets[-1]) - prev
        if not new_keys:
            verdict = "수렴 정지"
            detail = "마지막 회차에서 신규 위치 0건."
        else:
            new_f = [f for f in ok[-1].get("findings", [])
                     if comparison_key(f) in new_keys]
            if new_f and all(f.get("severity") in LOW_SEVERITIES for f in new_f):
                verdict = "효용 정지"
                detail = f"마지막 회차 신규 {len(new_keys)}건이 전부 low/style."

    if verdict is None and len(ok) >= args.cap:
        verdict = "예산 정지"
        detail = f"{args.cap}회 소진, 신규 발견이 계속 나옴. 잔존 리스크 미지수."

    # --- 드리프트 (도구 관측) ---
    if args.show_drift:
        drifts = report_drift(ok)
        if drifts:
            print("\n" + "-" * 60)
            print("도구 관측: 회차 간 분류 변동 (코드 발견 아님)")
            for k, field, vals, allowed in drifts:
                print(f"  {k:24s} {field:9s} {vals}  (위치당 결함 {allowed}건)")
            if any(f == "category" for _, f, _, _ in drifts):
                print("\n  category 변동은 비교 키에서 제외되어 판정에 영향 없음.")
            if any(f == "severity" for _, f, _, _ in drifts):
                print("  severity 변동은 효용 정지 판정에 영향을 줄 수 있음. 주의.")

    print("-" * 60)
    if verdict is None:
        print(f"판정: 계속 - {args.cap - len(ok)}회 남음")
        print("조치: 다음 회차를 새 서브에이전트로 실행 (fresh)")
        return 0

    print(f"판정: {verdict}")
    print(f"근거: {detail}")

    if verdict.startswith("예산"):
        print("\n주의: '수렴'과 의미가 다르다. 더 찾을 게 남았을 가능성이 높다.")
        print("조치: 재실행 또는 상한 상향 검토. 최종 보고에 미지수임을 명시.")
    else:
        print("\n의미: '버그 없음'이 아니라 '이 예산에서 더 안 나온다'.")

    print("\n정지는 승인이 아니다. phase 종료 여부는 사람이 판단.")
    print(f"발견 상세는 {vdir}/round_*.md 참조 (필요한 항목만 열어볼 것)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
