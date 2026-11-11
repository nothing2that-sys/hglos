#!/usr/bin/env python3
"""계획서에서 `Current State` 절을 뺀 나머지의 SHA-256 을 낸다 (커밋 금지 환경의 승인 후 변경 증명).

    python plan_hash.py <계획서.md>
    python plan_hash.py <계획서.md> --expect <승인 시점에 기록한 해시>

`Current State` 는 구현이 진행되며 갱신되는 절이라 승인 대상이 아니다. 그 밖의 모든 내용(공백 1개 포함)은
해시에 들어가며, 절의 위치도 반영한다(절을 옮기면 값이 바뀐다). CRLF 와 LF 는 같은 값이다.
제외 구간은 코드 펜스(``` 또는 ~~~, 여는 길이 이상의 같은 문자로만 닫힘) 밖의 ATX 제목(앞 공백 0~3칸, 빈 `##` 포함) 중
'Current State' 를 포함한 절 1개다:
제목 줄부터 같거나 높은 수준의 다음 제목 전까지이며 하위 제목을 포함한다.
펜스 안의 예시 제목은 절이 아니다.

종료 코드: 0 = 해시 출력(--expect 가 있으면 일치), 1 = --expect 불일치, 2 = 입력 오류
(파일 없음, Current State 절 없음 또는 2개 이상).
"""

import argparse
import hashlib
import re
import sys

TITLE = "Current State"
# CommonMark: up to 3 spaces of indentation before an ATX heading or a fence.
# An empty `##` is a heading too; a closing `#` run only counts after whitespace (so `## C#` keeps its `#`).
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MARKER = "\0CURRENT_STATE\0\n"


def digest(text):
    """Current State 절을 표지로 바꾼 본문의 SHA-256 (64자 hex). 절이 없거나 2개 이상이면 ValueError."""
    lines = text.replace("\r\n", "\n").split("\n")
    kept, fence, level, found = [], None, None, 0
    for line in lines:
        m = FENCE.match(line)
        marker = False
        if fence is None:
            # A backtick fence's info string may not contain backticks.
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence, marker = m.group(1), True
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            # Closes only with the same character, at least as long, and nothing but whitespace after it.
            fence, marker = None, True
        head = None if fence is not None or marker else HEADING.match(line)
        if level is not None and head and len(head.group(1)) <= level:
            level = None
        title = re.sub(r"^#+$", "", (head.group(2) or "").strip()) if head else ""  # `## ##` is empty
        if head and TITLE in title:
            # Every Current State heading counts, also one nested inside the excluded section.
            found += 1
            if level is None:
                level = len(head.group(1))
                kept.append(MARKER.rstrip("\n"))
        if level is None:
            kept.append(line)
    if found != 1:
        raise ValueError(f"'{TITLE}' 절이 정확히 1개여야 한다: {found}개 발견")
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Hash a Slice_Plan outside its Current State section")
    ap.add_argument("plan")
    ap.add_argument("--expect", help="승인 시점에 기록한 해시. 다르면 종료 코드 1")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        with open(args.plan, "rb") as f:
            value = digest(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    print(value)
    if args.expect is not None and args.expect.strip().lower() != value:
        print(f"불일치: 승인 후 Current State 밖이 바뀌었다 (기대 {args.expect.strip()})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
