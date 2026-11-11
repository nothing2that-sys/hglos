#!/usr/bin/env python3
"""Render a source-linked project snapshot from explicitly selected Markdown files."""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

from visuals import (STATES, concern_bars, diagram_sources, plan_namespace, prepare_view,
                     project_model, readable_lines, render_views, task_anchor, task_counts)

# Source checkout uses shared scripts; installed skill bundles carry them locally.
shared = Path(__file__).resolve().parents[3] / 'scripts'
if shared.is_dir():
    sys.path.insert(0, str(shared))
from coordination_view import (SESSION_CSS, activity, load_sessions, session_model,
                               sessions_html, sessions_markdown)

GROUPS = {
    "Current State": ("current state", "현재 상태"),
    "종결 조건": ("종결 조건",),
    "미결": ("미결",),
    "작업 분할": ("작업 분할",),
}
SIGNATURE = "<!-- HGLOS-DASHBOARD v1 -->\n"
END_SIGNATURE = "\n<!-- /HGLOS-DASHBOARD -->\n"


def load_source(repo, name):
    path = (repo / name).resolve()
    if not path.is_relative_to(repo) or path.suffix.lower() != ".md":
        raise ValueError(f"Expected Markdown inside repository: {name}")
    raw = path.read_bytes()
    return {"path": path, "name": path.relative_to(repo).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "lines": raw.decode("utf-8-sig").splitlines()}


def sections(lines):
    """ATX headings outside fences. Keep raw lines; never interpret approval text."""
    result = []
    fence = None
    for number, line in enumerate(readable_lines(lines), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        heading = None if fence or marker else re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            result.append({"level": len(heading.group(1)), "title": heading.group(2), "line": number})
    for i, item in enumerate(result):
        end = next((x["line"] - 1 for x in result[i + 1:] if x["level"] <= item["level"]), len(lines))
        item["text"] = "\n".join(lines[item["line"] - 1:end])
    return result


def select_sections(source):
    found = sections(source["lines"])
    selected = []
    for label, names in GROUPS.items():
        matches = [x for x in found if any(n in x["title"].lower() for n in names)]
        selected.append((label, matches))
    return selected


def concern_counts(lines):
    """Count explicit status-column labels only, excluding code examples/history."""
    counts = Counter()
    column = None
    fence = None
    for line in readable_lines(lines):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            column = None
            continue
        if fence:
            continue
        if not line.strip().startswith("|"):
            column = None
            continue
        cells = [x.strip() for x in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if "상태" in cells or "Status" in cells:
            column = cells.index("상태") if "상태" in cells else cells.index("Status")
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        if column is not None:
            status = cells[column] if column < len(cells) else ""
            counts[status or "미해석(빈 상태)"] += 1
    return counts


def source_ref(repo, source, output=None):
    """Percent-encoded POSIX path to the source, relative to the output file (or to the repo root when unknown)."""
    base = output.parent if output is not None else repo
    try:
        relative = Path(os.path.relpath(source["path"], base)).as_posix()
    except ValueError:
        relative = source["name"]
    return quote(relative, safe="/")


def source_html(source, ref=None):
    return (f'<a href="{escape(ref or quote(source["name"], safe="/"), quote=True)}">{escape(source["name"])}</a>'
            f'<small>SHA-256 {source["sha256"]}</small>')


def approval_excerpt(source):
    """Preserve the same bounded raw approval lines in every output format."""
    approvals = [(i, line) for i, line in enumerate(source["lines"], 1)
                 if "승인" in line and "|" not in line][:12]
    return "\n".join(f"L{i}: {line}" for i, line in approvals) or "해당 원문 없음 · 미검증"


def overview_html(prepared):
    counts = Counter({key: 0 for key in STATES})
    for view in prepared:
        counts.update(task_counts(view["model"]))
    total = sum(counts.values())
    metrics, segments, queues = [], [], []
    for key in ("active", "blocked", "done", "pending", "unknown"):
        label, symbol, _, _ = STATES[key]
        metrics.append(f'<a class="status-metric {key}" href="#queue-{key}" data-task-state="{key}" data-count="{counts[key]}">'
                       f'<span class="metric-label">{symbol} {label}</span><strong>{counts[key]}</strong><span class="metric-unit">문서 작업</span></a>')
        if counts[key]:
            segments.append(f'<span class="segment {key}" style="width:{counts[key] / total * 100:.6f}%" title="{label} {counts[key]}"></span>')
        rows = []
        for view in prepared:
            for task in view["model"]["tasks"]:
                if task["state"] == key:
                    rows.append(f'<li><a href="#{task_anchor(view["source"], task)}">{escape(task["name"])}</a>'
                                f'<span>{escape(view["source"]["name"])} · L{task["line"]}</span></li>')
        queues.append(f'<details class="status-queue" id="queue-{key}"><summary>{symbol} {label} · {counts[key]}개</summary><ul>' +
                      (''.join(rows) or '<li>해당 상태의 작업 정의 없음</li>') + '</ul></details>')
    warnings = sum(len(v["model"]["warnings"]) + len(v["model"]["graph_errors"]) for v in prepared)
    note = '작업 분할 표 없음 · 작업 집계 미검증' if not total else '각 계획서의 작업 정의 행 기준 · 완료율/승인 판정 아님'
    distribution = ' · '.join(f'{STATES[k][0]} {counts[k]}' for k in STATES)
    main = ('<section class="dashboard-overview" id="hglos-overview" aria-label="프로젝트 상태 요약">'
            '<div class="section-heading"><h2>한눈에 보는 작업 상태</h2>'
            f'<span class="warning-count" data-warning-count="{warnings}">입력·관계 경고 {warnings}</span></div>'
            '<div class="status-metrics">' + ''.join(metrics) + '</div>'
            f'<div class="status-segments" role="img" aria-label="{escape(distribution, quote=True)}">' + ''.join(segments) + '</div>'
            f'<p class="muted">작업 정의 {total}행 · {note}</p></section>')
    lists = '<section class="queues-panel" aria-label="상태별 작업 찾아보기"><h2>상태별 작업 찾아보기</h2>' + ''.join(queues) + '</section>'
    return main, lists


def session_glance(data):
    model = session_model(data)
    cards = []
    for index, row in enumerate(model["sessions"], 1):
        cards.append(f'<a class="session-glance-card" href="#hglos-session-{index}"><strong>{escape(row["host"])} · {escape(row["id"])}</strong>'
                     f'<span>{escape(activity(row))}</span><span>{escape(row["task"] or "목적 미기록")}</span>'
                     f'<small>선점 {len(row["claims"])} · 미종료 호출 {len(row["operations"])}</small></a>')
    return ('<section class="session-glance" id="hglos-session-overview"><h2>누가 무엇을 신고했나</h2>'
            '<p class="muted">수집 시점의 신고 기록 · 세션을 눌러 소유 관계와 전체 선점을 펼칩니다.</p>'
            '<div class="session-glance-grid">' + (''.join(cards) or '<p>등록된 세션 없음</p>') + '</div></section>')


def render_html(repo, plans, concerns, generated, sessions=None, output=None):
    prepared = [prepare_view(source) for source in plans]
    overview, queues = overview_html(prepared)
    cards = []
    missing = 0
    for view in prepared:
        source, model = view["source"], view["model"]
        prefix = plan_namespace(source)
        body = [render_views(source, model, view["diagrams"])]
        body.append(f'<details class="source-meta" id="provenance-{prefix}"><summary>원본 문서와 SHA-256 확인</summary>{source_html(source, source_ref(repo, source, output))}</details>')
        body.append(f'<details><summary>승인 관련 원문 · 유효성 미검증</summary><pre>{escape(approval_excerpt(source))}</pre></details>')
        for label, matches in select_sections(source):
            if not matches:
                missing += 1
                body.append(f'<section><h3>{escape(label)} <span class="badge">미검증 · 절 없음</span></h3></section>')
            for item in matches:
                body.append(f'<details><summary>원문 · {escape(label)} <span class="badge">L{item["line"]}</span></summary>'
                            f'<pre>{escape(item["text"])}</pre></details>')
        title = next((x["title"] for x in sections(source["lines"]) if x["level"] == 1), source["name"])
        cards.append(f'<article class="plan-card" id="plan-{prefix}"><div class="section-heading"><h2>{escape(title)}</h2>'
                     f'<a href="#provenance-{prefix}">원본 근거</a></div>{"".join(body)}</article>')
    if concerns:
        counts = concern_counts(concerns["lines"])
        summary = " · ".join(f"{label}: {n}" for label, n in sorted(counts.items())) or "상태 열 미해석 · 미검증"
        numbered = "\n".join(f"L{i}: {line}" for i, line in enumerate(concerns["lines"], 1))
        cards.append(f'<article class="wide"><h2>우려 대장</h2>{source_html(concerns, source_ref(repo, concerns, output))}{concern_bars(counts)}<p>{escape(summary)}</p>'
                     f'<details><summary>대장 원문 보기</summary><pre>{escape(numbered)}</pre></details></article>')
    if sessions is not None:
        cards.append(session_glance(sessions))
        cards.append(sessions_html(sessions))
    css = """
    :root{color-scheme:light;font-family:Segoe UI,Malgun Gothic,sans-serif;color:#193249;background:#f0f4f8}
    *{box-sizing:border-box}body{margin:0;padding:36px;max-width:1600px;margin-inline:auto}
    header{padding:30px;border-radius:18px;background:#13364a;color:#fff;margin-bottom:24px}
    .eyebrow{color:#94e4d5;font-weight:700;letter-spacing:.13em;font-size:12px}h1{font-size:30px;margin:12px 0}
    header p{color:#d1e2ed;line-height:1.7;max-width:900px}.metrics{display:flex;gap:16px;flex-wrap:wrap;margin-top:24px}
    .metric{padding:14px 22px;border:1px solid #4b697b;border-radius:10px}.metric strong{font-size:24px;margin-right:8px}
    main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,500px),1fr));gap:22px}
    article{background:white;border:1px solid #d6e0e8;border-radius:14px;padding:24px;min-width:0;box-shadow:0 5px 20px #19324908}
    h2{margin:0 0 14px;font-size:21px}h3{font-size:15px;margin:20px 0 10px}.badge{display:inline-block;font-size:11px;padding:4px 7px;background:#eaf3f7;color:#3d596d;border-radius:5px}
    a{color:#17628e;overflow-wrap:anywhere}small{display:block;color:#617487;font-size:11px;overflow-wrap:anywhere;margin:8px 0 18px}
    pre{font:13px/1.8 Consolas,Malgun Gothic,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fb;border-left:3px solid #70a8b7;padding:14px;border-radius:5px}
    details{margin-top:16px}summary{cursor:pointer;color:#346275;font-size:14px}.wide{grid-column:1/-1}
    article:has(.task-board){grid-column:1/-1}figure{margin:18px 0;overflow-x:auto;padding-bottom:8px}
    figcaption{font-size:12px;color:#617487;line-height:1.7;margin:8px 0}.workflow svg{width:100%;height:auto;min-width:560px;max-width:1000px}.dependency svg{display:block;max-width:none;height:auto}
    .visual-note{padding:10px 14px;border-radius:6px;background:#eef3f8;color:#4e6175;font-size:12px}
    .resume-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.resume{padding:12px 16px;border:1px solid #d6e0e8;border-radius:10px}.resume h3{margin:0;font-size:13px}.resume p{font-size:14px;line-height:1.7}
    .task-board{display:grid;grid-template-columns:repeat(5,minmax(170px,1fr));gap:12px;overflow-x:auto;padding-bottom:8px}
    .lane{padding:12px;background:#f5f7fa;border-top:4px solid #8295a6;border-radius:8px}.lane h4{font-size:13px;margin:0 0 12px}.lane h4 span{float:right}.lane.active{border-color:#1c5794}.lane.done{border-color:#24624d}.lane.blocked{border-color:#8b461c}.lane.unknown{border-style:dashed;border-color:#665182}
    .task{margin:10px 0;padding:12px;background:white;border:1px solid #dde5ed;border-radius:8px;overflow-wrap:anywhere}.task p{font-size:12px;line-height:1.7}.task small{margin:8px 0}.task summary{font-size:12px}
    .table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px;border:1px solid #d6e0e8;text-align:left;line-height:1.7;min-width:100px}th{background:#f0f5f8}
    .warnings{padding:14px;background:#fff2e7;border-left:4px solid #a46335;margin:18px 0;font-size:13px;overflow-wrap:anywhere}.warnings li{margin:7px 0}.phase-evidence{font-size:12px;color:#617487;overflow-wrap:anywhere}
    .bar-row{display:grid;grid-template-columns:minmax(100px,180px) 1fr 40px;gap:12px;align-items:center;margin:12px 0;font-size:13px}.bar-track{height:14px;background:#edf1f5;border-radius:4px}.bar-fill{height:100%;background:#52899a;border-radius:4px}
    footer{font-size:12px;color:#617487;margin:22px 0} @media(max-width:600px){body{padding:14px}header{padding:20px}article{padding:18px}h1{font-size:24px}}
    """
    css += SESSION_CSS + Path(__file__).with_name("dashboard.css").read_text(encoding="utf-8")
    plan_links = ''.join(f'<a href="#plan-{plan_namespace(v["source"])}">{escape(v["source"]["name"])}</a>' for v in prepared)
    if sessions is not None:
        plan_links += f'<a href="#hglos-session-overview">세션 {len(sessions["sessions"])}</a>'
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>HGLOS · {escape(repo.name)}</title><style>{css}</style></head><body>'
            f'<header class="page-header"><div><div class="eyebrow">HGLOS / PROJECT SNAPSHOT</div><h1>{escape(repo.name)} 프로젝트 대시보드</h1>'
            f'<p>상태 → 작업 흐름 → 근거. 문서 정본 · 승인/완료 유효성 미검증</p></div>'
            f'<div class="snapshot-stamp">생성 {escape(generated)}<br>수동 생성 · 실시간 갱신 아님</div></header>'
            f'<nav class="project-nav" aria-label="계획서와 세션 바로가기">{plan_links}</nav>'
            f'{overview}{queues}<main>{"".join(cards)}</main><footer>계획서 {len(plans)} · 우려 대장 {1 if concerns else 0} · 누락 절 {missing} (미검증). '
            '명시적으로 제공된 세션 스냅샷 외에 실시간 세션·장비 상태와 자동 진행률은 수집하지 않습니다. '
            '상태 집계는 원문 표기의 집계입니다. 입력 SHA-256은 생성 시 읽은 바이트 기준입니다.</footer></body></html>')


def render_markdown(repo, plans, concerns, generated, sessions=None, output=None):
    result = [f"# {repo.name} 프로젝트 대시보드", f"생성: {generated}",
              "승인·완료 유효성 미검증. 원본 문서가 정본이며 이 문서는 파생 스냅샷입니다."]
    for source in plans:
        result += [f"## {source['name']}", f"원본: {source_ref(repo, source, output)}", f"SHA-256: {source['sha256']}"]
        result += ["### 승인 관련 원문 · 유효성 미검증",
                   "\n".join("    " + line for line in approval_excerpt(source).splitlines())]
        model = project_model(source)
        for title, (_, code) in diagram_sources(model).items():
            result += [f"### {title} · 문서 표기", "```mermaid\n" + code + "```"]
        for label, matches in select_sections(source):
            result.append(f"### {label}")
            if not matches:
                result.append("미검증 · 절 없음")
            for item in matches:
                result.append(f"원문 L{item['line']}")
                result.extend("> " + line for line in item["text"].splitlines())
    if concerns:
        result += ["## 우려 대장", f"원본: {source_ref(repo, concerns, output)}", f"SHA-256: {concerns['sha256']}",
                   "상태 원문 집계: " + str(dict(concern_counts(concerns["lines"])))]
        result.extend("> " + line for line in concerns["lines"])
    if sessions is not None:
        result.append(sessions_markdown(sessions))
    return "\n\n".join(result) + "\n"


def prepare_exports(repo, directory, plans):
    folder = (repo / directory).resolve()
    if not folder.is_relative_to(repo):
        raise ValueError("Diagram directory must be inside repository")
    outputs = []
    for index, source in enumerate(plans, 1):
        slug = re.sub(r"[^\w-]", "-", source["path"].stem)[:60] or "plan"
        for kind, (svg, mermaid) in diagram_sources(project_model(source)).items():
            stem = f"{index:02d}-{slug}-{kind}"
            provenance = f"Source: {source['name']} SHA-256 {source['sha256']}"
            svg = svg.replace(">", "><metadata>" + escape(provenance) + "</metadata>", 1)
            outputs += [(folder / f"{stem}.svg", SIGNATURE + svg + END_SIGNATURE),
                        (folder / f"{stem}.mmd", "%% HGLOS-DIAGRAM v1\n%% " + provenance.replace("\n", " ") + "\n" + mermaid + "%% /HGLOS-DIAGRAM\n")]
    return outputs


def write_outputs(repo, outputs, inputs, overwrite):
    prepared = []
    paths = [path.resolve() for path, _ in outputs]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate output path")
    for path, content in outputs:
        target = path.resolve()
        if not target.is_relative_to(repo) or target in inputs:
            raise ValueError("Output must stay inside repository and cannot replace an input")
        if target.exists():
            old = target.read_text(encoding="utf-8")
            generated = (old.startswith(SIGNATURE) and old.endswith(END_SIGNATURE)) or (old.startswith("%% HGLOS-DIAGRAM v1\n") and old.endswith("%% /HGLOS-DIAGRAM\n"))
            if not overwrite or not generated:
                raise ValueError(f"Output exists; only generated HGLOS files may be updated with --overwrite: {target}")
        prepared.append((target, content))
    staged = []
    try:
        for target, content in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".hglos-dashboard-", dir=target.parent)
            staged.append((Path(temp), target))
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        for temp, target in staged:
            os.replace(temp, target)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--plan", action="append", type=Path, default=[])
    ap.add_argument("--concerns", type=Path)
    ap.add_argument("--sessions", type=Path, help="Explicit coordination.py snapshot JSON; no live DB access")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--diagram-dir", type=Path, help="Also export generated SVG and Mermaid sources inside repository")
    args = ap.parse_args()
    try:
        repo = args.repo.resolve(strict=True)
        if not repo.is_dir():
            raise ValueError("Repository must be a directory")
        plans = [load_source(repo, p) for p in args.plan]
        concerns = load_source(repo, args.concerns) if args.concerns else None
        sessions = load_sessions(repo, args.sessions) if args.sessions else None
        if not plans and sessions is None:
            raise ValueError('Provide at least one --plan or a --sessions snapshot')
        inputs = plans + ([concerns] if concerns else [])
        if len({s["path"] for s in inputs}) != len(inputs):
            raise ValueError("Duplicate input document")
        output = (repo / args.output).resolve()
        if output in {s["path"] for s in inputs}:
            raise ValueError("Output cannot overwrite an input document")
        if not output.is_relative_to(repo) or output.suffix.lower() not in (".html", ".md"):
            raise ValueError("Output must be .html or .md inside repository")
        generated = datetime.now().astimezone().isoformat(timespec="seconds")
        render = render_html if output.suffix.lower() == ".html" else render_markdown
        content = SIGNATURE + render(repo, plans, concerns, generated, sessions, output=output).rstrip("\n") + END_SIGNATURE
        outputs = [(output, content)]
        if args.diagram_dir:
            outputs.extend(prepare_exports(repo, args.diagram_dir, plans))
        protected = {s["path"] for s in inputs}
        if sessions is not None:
            protected.add(sessions['path'])
        write_outputs(repo, outputs, protected, args.overwrite)
        print(f"Dashboard: {output}")
        for path, _ in outputs[1:]:
            print(f"Diagram: {path}")
        for source in inputs:
            print(f"{source['name']} sha256 {source['sha256']}")
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
