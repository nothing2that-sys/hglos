#!/usr/bin/env python3
"""두 목록을 양방향 차집합으로 대조한다. 개수 대조가 아니라 이름 대조다.

    python set_diff.py <기대.txt> <실제.txt>
    <명령> | python set_diff.py <기대.txt> -

한 줄에 항목 하나. 앞뒤 공백과 BOM 은 지운다. 빈 줄은 무시한다.
'#' 줄은 --comments 를 줄 때만 무시한다 ('#12' 같은 항목이 조용히 빠지지 않게).
양쪽이 모두 비어 있으면 입력 오류다 (빈 것끼리 같다는 판정은 아무것도 확인하지 않은 것이다).
중복 항목은 따로 보고한다 (개수가 맞아도 중복이 숨길 수 있다).

종료 코드: 0 = 같다, 1 = 다르다, 2 = 입력 오류.
"""

import sys
from collections import Counter


class InputError(Exception):
    pass


def read_items(path, comments=False):
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
    except FileNotFoundError:
        raise InputError(f"파일이 없다: {path}")
    except UnicodeDecodeError:
        raise InputError(f"UTF-8 로 읽을 수 없다: {path}")
    text = text.lstrip("\ufeff")
    items = [ln.strip() for ln in text.splitlines()]
    items = [x for x in items if x]
    if comments:
        items = [x for x in items if not x.startswith("#")]
    return items


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    comments = "--comments" in argv
    argv = [a for a in argv if a != "--comments"]
    if len(argv) != 2 or argv.count("-") > 1:
        print("사용법: set_diff.py [--comments] <기대> <실제>   ('-' 는 표준입력, 한쪽만)", file=sys.stderr)
        return 2
    try:
        exp, act = read_items(argv[0], comments), read_items(argv[1], comments)
        if not exp and not act:
            raise InputError("양쪽 목록이 모두 비었다")
    except InputError as e:
        print(f"입력 오류 - 판정하지 않는다: {e}", file=sys.stderr)
        return 2
    se, sa = set(exp), set(act)
    missing, extra = sorted(se - sa), sorted(sa - se)
    dup_e = sorted(k for k, v in Counter(exp).items() if v > 1)
    dup_a = sorted(k for k, v in Counter(act).items() if v > 1)
    print(f"기대 {len(exp)}행 (고유 {len(se)}) / 실제 {len(act)}행 (고유 {len(sa)})")
    print(f"기대에만 있음 {len(missing)}: {', '.join(missing) or '-'}")
    print(f"실제에만 있음 {len(extra)}: {', '.join(extra) or '-'}")
    if dup_e or dup_a:
        print(f"중복 - 기대: {', '.join(dup_e) or '-'} / 실제: {', '.join(dup_a) or '-'}")
    same = not missing and not extra and not dup_e and not dup_a
    print("판정: 같다" if same else "판정: 다르다")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
