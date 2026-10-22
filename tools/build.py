#!/usr/bin/env python3
"""공용 스크립트를 각 스킬에 동기화하고, 스킬 번들(.skill) 3개를 결정적으로 만든다.

    python tools/build.py           # 동기화 + dist/*.skill 생성 + 검증
    python tools/build.py --check   # 아무것도 쓰지 않고, 사본이 정본과 같은지만 검사 (다르면 exit 1)

정본: shared/scripts/*.py
사본: plugins/hglos/skills/<스킬>/scripts/ (Claude Code 플러그인으로 설치할 때 스킬이 자기 폴더의 scripts/ 를 쓰기 때문)
번들: dist/<스킬>.skill = plugins/hglos/skills/<스킬>/ 를 그대로 zip. 타임스탬프 고정·엔트리 정렬 - 같은 입력이면 같은 바이트.
"""

import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "shared" / "scripts"
SKILLS_DIR = REPO / "plugins" / "hglos" / "skills"
SKILLS = ["hglos-design", "hglos-implement", "hglos-verify"]
DIST = REPO / "dist"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def shared_files():
    files = sorted(SHARED.glob("*.py"))
    if not files:
        raise SystemExit(f"공용 스크립트가 없다: {SHARED}")
    return files


def check_sync():
    bad = []
    for skill in SKILLS:
        for src in shared_files():
            dst = SKILLS_DIR / skill / "scripts" / src.name
            if not dst.is_file() or dst.read_bytes() != src.read_bytes():
                bad.append(dst.relative_to(REPO).as_posix())
    return bad


def sync():
    for skill in SKILLS:
        d = SKILLS_DIR / skill / "scripts"
        d.mkdir(parents=True, exist_ok=True)
        for src in shared_files():
            shutil.copyfile(src, d / src.name)


def items(skill):
    root = SKILLS_DIR / skill
    if not (root / "SKILL.md").is_file():
        raise SystemExit(f"SKILL.md 가 없다: {root}")
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_dir() or any(x in SKIP_DIRS for x in rel.parts) or p.suffix in SKIP_SUFFIXES:
            continue
        out.append((f"{skill}/{rel.as_posix()}", p))
    return sorted(out)


def build(skill, its):
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{skill}.skill"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, src in its:
            zi = zipfile.ZipInfo(arc, date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 3
            z.writestr(zi, src.read_bytes())
    return out


def verify(out, its):
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="hglos-verify-bundle-"))
    try:
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            z.extractall(tmp)
        expected = {a for a, _ in its}
        problems += [f"번들에만 있다: {x}" for x in sorted(names - expected)]
        problems += [f"번들에서 빠졌다: {x}" for x in sorted(expected - names)]
        for arc, src in its:
            got = tmp / arc
            if not got.is_file() or got.read_bytes() != src.read_bytes():
                problems.append(f"바이트가 다르다: {arc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


def main(argv):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if "--check" in argv:
        bad = check_sync()
        for b in bad:
            print(f"[다름] {b}", file=sys.stderr)
        print("동기화 상태: " + ("불일치" if bad else "일치"))
        return 1 if bad else 0
    sync()
    rc = 0
    for skill in SKILLS:
        its = items(skill)
        out = build(skill, its)
        problems = verify(out, its)
        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        if problems:
            rc = 1
            print(f"[실패] {skill}", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
        else:
            print(f"[통과] {out.relative_to(REPO).as_posix()}  {len(its)} 파일  sha256 {sha}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
