#!/usr/bin/env python3
"""닫힌 항목 ID 가 아직 '대기·미결' 맥락에 남아 있는 줄을 찾는다 (문면 노후화 검출).

    python stale_ids.py --ledger <대장.md> <검사할 파일 또는 폴더> [...]
    python stale_ids.py --ids CN-01,OI-033 <검사할 파일 또는 폴더> [...]

대장은 마크다운 표다. 첫 열이 ID, 헤더에 '상태' 또는 'status' 열이 있어야 한다.
상태가 닫힘 계열(CLOSED / DROPPED / DONE / 닫힘 / 폐기 / 완료)인 행의 ID 를 모은다.

같은 줄에 닫힌 ID 와 대기 표현(--words)이 함께 있으면 갈라진 지점으로 보고한다.
대기 표현 "- [ ]" 은 특수값이다 - 줄머리의 미완료 체크박스(`- [ ]`·`* [ ]`·`+ [ ]`·`1. [ ]`, 인용 `> ` 안 포함)를 뜻한다.
정확히 "- [ ]" 일 때만 줄 형태로 판정하고, 다른 표기(예: "[ ]")는 일반 표현처럼 부분 문자열로 찾는다.
코드 펜스 안의 예시 줄도 검사한다(펜스를 구분하지 않는다).
(근거: 닫힌 마일스톤 M1 을 「- [ ] ... M1 기준 회복」 체크박스 항목이 계속 열린 일로 들고 있던 실사례)
대장 파일 자체는 검사 대상에서 뺀다.

종료 코드: 0 = 발견 없음, 1 = 발견 있음, 2 = 입력 오류.
닫힌 ID 를 하나도 못 모았으면 '발견 없음'이 아니라 입력 오류다 (조용한 통과 금지).
"""

import argparse
import os
import re
import sys

CLOSED = {"closed", "dropped", "done", "닫힘", "폐기", "완료"}
CHECKBOX = "- [ ]"  # 특수 표현: 줄머리의 미완료 체크박스. 부분 문자열이 아니라 줄 형태로 판정
CHECKBOX_RE = re.compile(r"^\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[ \]")
DEFAULT_WORDS = ["대기", "미확정", "남은", "미결", "미해결", "진행 중", "OPEN", "TODO", "TBD", "pending", CHECKBOX]
EXTS = {".md", ".txt"}


class InputError(Exception):
    pass


def split_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


CLOSED_PREFIX = ("닫힘", "폐기", "완료", "종료", "해소")


def status_word(cell):
    """'**CLOSED (2026-10-02)**' -> 'closed', '✅ 완료됨' -> '완료됨'. 상태 칸의 첫 낱말."""
    t = cell.replace("*", "").replace("`", "").strip()
    t = re.sub(r"^[^\w가-힣]+", "", t)
    t = re.split(r"[\s(（:,\-_/]", t, maxsplit=1)[0]
    return t.lower()


def is_closed(word):
    return word in CLOSED or word.startswith(CLOSED_PREFIX)


def id_cell(cell):
    """'[CN-04](#cn-04)' -> 'CN-04', '**CN-04**' -> 'CN-04'"""
    t = cell.strip().strip("*` ")
    m = re.match(r"^\[([^\]]+)\]\([^)]*\)$", t)
    return (m.group(1) if m else t).strip("*` ")


def closed_ids_from_ledger(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        raise InputError(f"대장 파일이 없다: {path}")
    ids = set()
    seen = set()
    status_col = None
    for line in lines:
        if not line.lstrip().startswith("|"):
            status_col = None
            continue
        cells = split_row(line)
        lowered = [c.lower() for c in cells]
        if "상태" in cells or "status" in lowered:
            status_col = cells.index("상태") if "상태" in cells else lowered.index("status")
            continue
        if status_col is None or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        if status_col < len(cells) and cells[0]:
            st = status_word(cells[status_col])
            if is_closed(st):
                ids.add(id_cell(cells[0]))
            else:
                seen.add(st)
    return ids, seen


def iter_files(targets, skip):
    for t in targets:
        if os.path.isfile(t):
            if os.path.abspath(t) != skip:
                yield t
        elif os.path.isdir(t):
            for root, dirs, names in os.walk(t):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules"}]
                for n in sorted(names):
                    p = os.path.join(root, n)
                    if os.path.splitext(n)[1].lower() in EXTS and os.path.abspath(p) != skip:
                        yield p
        else:
            raise InputError(f"검사 대상이 없다: {t}")


def scan(ids, words, targets, skip):
    id_re = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)) + r")(?![A-Za-z0-9_-])")
    hits = []
    for path in iter_files(targets, skip):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            raise InputError(f"UTF-8 로 읽을 수 없다 (판정 거부): {path}")
        for no, line in enumerate(text.splitlines(), 1):
            m = id_re.findall(line)
            if not m:
                continue
            w = [x for x in words
                 if (CHECKBOX_RE.match(line) if x == CHECKBOX else x.lower() in line.lower())]
            if w:
                hits.append((path, no, sorted(set(m)), w, line.strip()))
    return hits


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="닫힌 ID 가 대기 맥락에 남은 줄 찾기")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ledger")
    src.add_argument("--ids", help="쉼표 구분 닫힌 ID 목록")
    ap.add_argument("--words", help="쉼표 구분 대기 표현. 기본: " + ",".join(DEFAULT_WORDS))
    ap.add_argument("targets", nargs="+")
    args = ap.parse_args(argv)
    try:
        if args.ledger:
            ids, seen = closed_ids_from_ledger(args.ledger)
            skip = os.path.abspath(args.ledger)
        else:
            ids = {x.strip() for x in args.ids.split(",") if x.strip()}
            seen = set()
            skip = None
        if not ids:
            raise InputError("닫힌 ID 를 하나도 모으지 못했다. 대장 형식(첫 열 ID, '상태' 열)을 확인하라")
        words = [w.strip() for w in args.words.split(",")] if args.words else DEFAULT_WORDS
        words = [w for w in words if w]
        if not words:
            raise InputError("대기 표현 목록이 비었다")
        hits = scan(ids, words, args.targets, skip)
    except InputError as e:
        print(f"입력 오류 - 판정하지 않는다: {e}", file=sys.stderr)
        return 2
    print(f"닫힌 ID {len(ids)}개: {', '.join(sorted(ids))}")
    known_open = {"open", "보류", "진행", "대기", "열림"}
    odd = sorted(w for w in seen if w and w not in known_open)
    if odd:
        print(f"열린 것으로 취급한 상태값 (닫힘이면 대장 표기를 확인): {', '.join(odd)}")
    if not hits:
        print("발견 없음")
        return 0
    print(f"갈라진 지점 {len(hits)}곳:")
    for path, no, m, w, line in hits:
        print(f"  {path}:{no}  [{', '.join(m)}] + [{', '.join(w)}]  {line[:160]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
