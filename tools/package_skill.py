#!/usr/bin/env python3
"""HGLOS 스킬 번들과 통합 ZIP을 결정적으로 만든다.

    python tools/package_skill.py

하는 일 (스킬마다):
  1. plugin/hglos/skills/<스킬>/ 의 파일 + plugin/hglos/scripts/*.py (공용) 를
     skill/<스킬>.skill 로 zip 한다. 공용 스크립트는 번들 안 scripts/ 에 들어간다
  2. 번들을 풀어 각 엔트리를 원본 바이트와 대조한다. 다르면 실패한다
  3. 번들 sha256 을 찍는다

공용 스크립트의 정본은 plugin/hglos/scripts/ 하나다. 사본은 번들 안에만 생긴다(저장소 안에 사본을 두지 않는다).
스킬 자체 scripts/ 에 공용 스크립트와 같은 이름이 있으면 실패한다 - 어느 쪽이 들어갔는지 모르게 되기 때문이다.

결정적이다 - 타임스탬프 고정, 엔트리 정렬. 같은 입력이면 같은 바이트.
(2026-10-15 이전 판은 hglos-design 하나만 만들었다. git 21f13e2)
"""

import argparse
import hashlib
import sys
import tempfile
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin" / "hglos"
SKILLS = ["hglos-design", "hglos-implement", "hglos-verify", "hglos-dashboard", "hglos-coordinate"]
SHARED = PLUGIN / "scripts"
OUTDIR = REPO / "skill"

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = 0o644
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def walk(root):
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_dir() or any(x in SKIP_DIRS for x in rel.parts) or p.suffix in SKIP_SUFFIXES:
            continue
        out.append(rel)
    return out


def entries(skill):
    """(번들 안 경로, 원본 파일) 목록. 정렬됨."""
    src = PLUGIN / "skills" / skill
    if not (src / "SKILL.md").is_file():
        raise SystemExit(f"SKILL.md 가 없다: {src}")
    m = {f"{skill}/{rel.as_posix()}": src / rel for rel in walk(src)}
    for name in ("HOST.md", "RULES.md", "COORDINATION.md", "ENTRY.md", "context_routes.json", "context-handoff.md"):
        m[f"{skill}/{name}"] = PLUGIN / name
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        path = PLUGIN / name
        if path.read_bytes() != (REPO / name).read_bytes():
            raise SystemExit(f"License/notice differs from repository canonical copy: {path}")
        m[f"{skill}/{name}"] = path
    for p in sorted(SHARED.glob("*.py")):
        key = f"{skill}/scripts/{p.name}"
        if key in m:
            raise SystemExit(f"이름 충돌 - 스킬 자체 scripts/ 와 공용 scripts/ 에 같은 파일: {p.name}")
        m[key] = p
    return sorted(m.items())


def build(skill, items, outdir=OUTDIR):
    out = Path(outdir) / f"{skill}.skill"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, src in items:
            zi = zipfile.ZipInfo(arc, date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = FIXED_MODE << 16
            zi.create_system = 3
            z.writestr(zi, src.read_bytes())
    return out


def verify(out, items):
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="hglos-skill-verify-"))
    try:
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            z.extractall(tmp)
        expected = {a for a, _ in items}
        problems += [f"번들에만 있다: {x}" for x in sorted(names - expected)]
        problems += [f"번들에서 빠졌다: {x}" for x in sorted(expected - names)]
        for arc, src in items:
            got = tmp / arc
            if not got.is_file() or got.read_bytes() != src.read_bytes():
                problems.append(f"바이트가 다르다: {arc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


def main():
    ap = argparse.ArgumentParser(description="Build self-contained HGLOS skills and a portable ZIP")
    ap.add_argument("--outdir", type=Path, default=OUTDIR)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    rc = 0
    all_items = []
    for skill in SKILLS:
        items = entries(skill)
        all_items.extend(items)
        out = build(skill, items, args.outdir)
        problems = verify(out, items)
        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        if problems:
            rc = 1
            print(f"[실패] {skill}", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
        else:
            print(f"[통과] {out}  {len(items)} 파일  sha256 {sha}")
    portable = build("hglos-agent-skills", all_items, args.outdir)
    portable = portable.replace(portable.with_suffix(".zip"))
    if verify(portable, all_items):
        return 1
    print(f"[통과] {portable}  sha256 {hashlib.sha256(portable.read_bytes()).hexdigest()}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
