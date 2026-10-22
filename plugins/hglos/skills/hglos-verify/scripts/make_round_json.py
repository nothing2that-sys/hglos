#!/usr/bin/env python3
"""Codex 의 raw 출력을 round_<N>.json + round_<N>.md 로 변환한다.

지문(fp)을 여기서 조립한다. 모델에게 문자열 포매팅을 맡기지 않는다.
계약 위반은 기록하되 임의로 고치지 않는다 - 위반은 코드에 대한 발견이
아니라 도구에 대한 발견이며, 지문 규칙 재설계 신호다.

사용:
  python make_round_json.py <raw파일> --round N --outdir <validation경로>
                            [--exit-code N] [--stdout-log <파일>]
"""

import argparse
import json
import sys
from pathlib import Path

CATEGORIES = {
    "nullderef", "bounds", "leak", "race", "logic",
    "apicontract", "errorhandling", "perf", "style", "other",
}
SEVERITIES = {"critical", "high", "medium", "low", "style"}
EVIDENCE = {"observed", "inferred", "unconfirmed"}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "style"]

EVIDENCE_KO = {
    "observed": "관측사실",
    "inferred": "추론",
    "unconfirmed": "미확인",
}


def build_fp(path, line, category):
    """지문 조립. 결정론적이어야 한다."""
    p = str(path).replace("\\", "/").strip()
    ln = str(line) if isinstance(line, int) else "-"
    return f"{p}:{ln}:{category}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", help="codex --output-last-message 가 쓴 파일")
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--outdir", required=True, help="validation 디렉토리")
    ap.add_argument("--exit-code", type=int, default=0, help="codex 종료코드")
    ap.add_argument("--stdout-log", default=None, help="codex stdout 로그 (선택)")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    n = args.round

    json_path = outdir / f"round_{n}.json"
    md_path = outdir / f"round_{n}.md"

    violations = []
    findings = []
    invoked = True
    error = None
    raw_text = ""

    raw = Path(args.raw)

    # --- 호출 성공 여부 판정 ---
    if args.exit_code != 0:
        invoked = False
        error = f"codex exit code {args.exit_code}"
    elif not raw.exists():
        invoked = False
        error = f"출력 파일 없음: {raw}"
    else:
        raw_text = raw.read_text(encoding="utf-8", errors="replace").strip()
        if not raw_text:
            invoked = False
            error = "출력 파일이 비어있음"
        else:
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as e:
                invoked = False
                error = f"JSON 파싱 실패: {e}"
                data = None

            if invoked:
                if not isinstance(data, dict) or "findings" not in data:
                    violations.append("최상위에 'findings' 키가 없음")
                    raw_items = []
                else:
                    raw_items = data.get("findings") or []
                    if not isinstance(raw_items, list):
                        violations.append("'findings' 가 배열이 아님")
                        raw_items = []

                # --- 항목별 계약 검증 ---
                for idx, it in enumerate(raw_items):
                    if not isinstance(it, dict):
                        violations.append(f"findings[{idx}] 가 객체가 아님")
                        continue

                    cat = it.get("category")
                    sev = it.get("severity")
                    ev = it.get("evidence")
                    path = it.get("path")
                    line = it.get("line")

                    if cat not in CATEGORIES:
                        violations.append(f"findings[{idx}] category 위반: {cat!r}")
                    if sev not in SEVERITIES:
                        violations.append(f"findings[{idx}] severity 위반: {sev!r}")
                    if ev not in EVIDENCE:
                        violations.append(f"findings[{idx}] evidence 위반: {ev!r}")
                    if not path or not isinstance(path, str):
                        violations.append(f"findings[{idx}] path 없음/비문자열: {path!r}")
                    if line is not None and not isinstance(line, int):
                        violations.append(f"findings[{idx}] line 이 정수/null 아님: {line!r}")

                    # 위반이 있어도 버리지 않는다. 기록하고 담는다.
                    findings.append({
                        "fp": build_fp(path or "UNKNOWN", line, cat or "other"),
                        "severity": sev if sev in SEVERITIES else "other",
                        "evidence": ev if ev in EVIDENCE else "unconfirmed",
                        "claim": str(it.get("claim", "")).replace("\n", " ").strip(),
                        "detail": str(it.get("detail", "")).strip(),
                        "path": path,
                        "line": line,
                        "category": cat,
                    })

                # 중복 지문 병합
                merged = {}
                for f in findings:
                    key = f["fp"]
                    if key in merged:
                        merged[key]["count"] += 1
                    else:
                        f["count"] = 1
                        merged[key] = f
                if len(merged) != len(findings):
                    violations.append(
                        f"중복 지문 {len(findings) - len(merged)}건 병합됨"
                    )
                findings = list(merged.values())

    # --- round_N.json ---
    out = {
        "round": n,
        "codex_invoked": invoked,
        "contract_violations": violations,
        "report_path": f"validation/round_{n}.md",
        "findings": findings,
    }
    if error:
        out["error"] = error

    json_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- round_N.md ---
    def sev_key(f):
        s = f.get("severity")
        return SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else 99

    lines = [f"# 적대적 검증 보고서 - round {n}", ""]
    lines.append("## 호출 상태")
    lines.append(f"- codex_invoked: {invoked}")
    if error:
        lines.append(f"- 오류: {error}")
    lines.append(f"- 계약 위반: {len(violations)}건")
    for v in violations:
        lines.append(f"  - {v}")
    lines.append("")

    if findings:
        lines.append("## 발견 (심각도 순)")
        lines.append("")
        for f in sorted(findings, key=sev_key):
            lines.append(f"### [{f['severity']}] {f['fp']}")
            lines.append(f"- **주장**: {f['claim']}")
            if f.get("detail"):
                lines.append(f"- **상세**: {f['detail']}")
            ko = EVIDENCE_KO.get(f.get("evidence"), f.get("evidence"))
            lines.append(f"- **증거 등급**: {ko}")
            if f.get("count", 1) > 1:
                lines.append(f"- 병합된 중복: {f['count']}건")
            lines.append("")
    else:
        lines.append("## 발견")
        lines.append("")
        if invoked:
            lines.append("발견 없음. 잔존 리스크는 이 회차 예산 내에서 미검출이라는 의미일 뿐이다.")
        else:
            lines.append("호출 실패로 판정 불가.")
        lines.append("")

    if args.stdout_log and Path(args.stdout_log).exists():
        lines.append("## Codex stdout")
        lines.append("```")
        lines.append(Path(args.stdout_log).read_text(encoding="utf-8", errors="replace"))
        lines.append("```")
        lines.append("")

    lines.append("## Codex 최종 응답 원문")
    lines.append("```json")
    lines.append(raw_text if raw_text else "(없음)")
    lines.append("```")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    # --- 호출자용 한 줄 요약 ---
    max_sev = "none"
    for s in SEVERITY_ORDER:
        if any(f.get("severity") == s for f in findings):
            max_sev = s
            break

    print(f"round {n} 완료")
    print(f"- codex_invoked: {invoked}")
    if error:
        print(f"- 오류: {error}")
    print(f"- 지문 수: {len(findings)}")
    print(f"- 최고 심각도: {max_sev}")
    print(f"- 계약 위반: {'없음' if not violations else str(len(violations)) + '건'}")
    print(f"- 기록: {md_path.name}, {json_path.name}")

    return 0 if invoked else 1


if __name__ == "__main__":
    sys.exit(main())
