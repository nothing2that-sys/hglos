"""Source-grounded views: workflow, task board, dependency SVG/Mermaid and evidence.

Original implementation. Public skill design references are in references/visual-patterns.md.
Only explicit table cells supply state; completion/approval is never inferred from prose.
"""

from collections import Counter
from html import escape
import hashlib
import json
import re
import unicodedata

STATES = {
    "pending": ("대기", "○", "#eef2f6", "#526274"),
    "active": ("진행 중", "▶", "#e7f1ff", "#1c5794"),
    "done": ("완료", "✓", "#e3f4ed", "#24624d"),
    "blocked": ("차단", "!", "#fff0e5", "#8b461c"),
    "unknown": ("미검증", "?", "#f1edf7", "#665182"),
}
ALIASES = {
    "대기": "pending", "pending": "pending", "todo": "pending",
    "진행 중": "active", "진행중": "active", "in_progress": "active",
    "완료": "done", "done": "done", "completed": "done",
    "차단": "blocked", "blocked": "blocked", "미검증": "unknown", "unverified": "unknown",
}
PHASES = {"설계": "설계", "계획": "설계", "design": "설계", "구현": "구현", "implement": "구현",
          "검증": "검증", "verify": "검증"}


def plain(text):
    return text.strip().strip("`*").strip()


def state(raw):
    return ALIASES.get(plain(raw).lower(), "unknown")


def cells(line):
    return [p.strip().replace(r"\|", "|") for p in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def readable_lines(lines):
    """Mask fenced examples and HTML comments without changing source line numbers."""
    result, fence, comment = [], None, False
    for line in lines:
        if fence:
            closing = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            result.append("")
            continue
        visible, rest = "", line
        while rest:
            if comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    comment, rest = False, rest[end + 3:]
            else:
                start = rest.find("<!--")
                tick = rest.find("`")
                if tick >= 0 and (start < 0 or tick < start):
                    visible += rest[:tick]
                    segment = rest[tick:]
                    ticks = re.match(r"`+", segment).group(0)
                    closing = re.search(r"(?<!`)" + re.escape(ticks) + r"(?!`)", segment[len(ticks):])
                    end = len(ticks) + closing.end() if closing else len(ticks)
                    visible, rest = visible + segment[:end], segment[end:]
                    continue
                if start < 0:
                    visible, rest = visible + rest, ""
                else:
                    visible, rest, comment = visible + rest[:start], rest[start + 4:], True
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})", visible)
        if opening:
            fence = opening.group(1)
            visible = ""
        result.append(visible)
    return result


def tables(lines):
    """Read pipe tables with separator rows, heading ancestry and source line numbers."""
    lines = readable_lines(lines)
    result, headings = [], []
    fence = None
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            i += 1
            continue
        if fence:
            i += 1
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            headings = [h for h in headings if h[0] < level] + [(level, heading.group(2))]
        if line.strip().startswith("|") and i + 1 < len(lines):
            header, separator = cells(line), cells(lines[i + 1])
            if len(header) == len(separator) and all(re.fullmatch(r":?-{3,}:?", c) for c in separator):
                table = {"headers": [plain(h).lower() for h in header], "rows": [],
                         "section": " / ".join(h[1].lower() for h in headings), "line": i + 1}
                i += 2
                while i < len(lines) and lines[i].strip().startswith("|"):
                    values = cells(lines[i])
                    table["rows"].append({"values": values, "line": i + 1, "valid": len(values) == len(header)})
                    i += 1
                result.append(table)
                continue
        i += 1
    return result


def field(table, row, *names):
    for name in names:
        if name in table["headers"]:
            return row["values"][table["headers"].index(name)]
    return ""


def dependencies(raw):
    raw = plain(raw)
    if raw.lower() in ("", "-", "없음", "none", "[]"):
        return []
    if raw.startswith("["):
        values = json.loads(raw)
        if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() for x in values):
            raise ValueError("after must be a string array")
        return list(dict.fromkeys(plain(v) for v in values))
    return list(dict.fromkeys(plain(v) for v in re.split(r"[,;]", raw) if plain(v)))


def project_model(source):
    model = {"phases": {}, "tasks": [], "evidence": [], "warnings": [], "graph_errors": []}
    snapshots = {}
    for table in tables(source["lines"]):
        section = table["section"]
        current = "current state" in section or "현재 상태" in section
        for row in table["rows"]:
            line = row["line"]
            if not row["valid"]:
                model["warnings"].append(f"L{line}: 표 열 수 불일치 · 미해석")
                continue
            if current and "단계 현황" in section and "단계" in table["headers"]:
                phase_raw = plain(field(table, row, "단계"))
                phase = PHASES.get(phase_raw.lower())
                if phase is None:
                    model["warnings"].append(f"L{line}: 미해석 단계 {phase_raw}")
                    continue
                if phase in model["phases"]:
                    model["phases"][phase] = {"raw": "중복 상태 · 미검증", "line": line, "evidence": ""}
                    model["warnings"].append(f"L{line}: 중복 단계 {phase} · 미검증")
                else:
                    model["phases"][phase] = {"raw": field(table, row, "상태", "status"), "line": line,
                                              "evidence": field(table, row, "근거", "evidence")}
            elif current and "작업 현황" in section and "작업" in table["headers"]:
                key = plain(field(table, row, "작업"))
                if key in snapshots:
                    snapshots[key] = {"raw": "중복 상태 · 미검증", "evidence": "", "line": line}
                    model["warnings"].append(f"L{line}: 중복 작업 상태 {key} · 미검증")
                else:
                    snapshots[key] = {"raw": field(table, row, "상태", "status"),
                                      "evidence": field(table, row, "근거", "evidence"), "line": line}
            elif "작업 분할" in section and "작업" in table["headers"]:
                name = plain(field(table, row, "작업"))
                if not name:
                    continue
                try:
                    after = dependencies(field(table, row, "after", "선행"))
                except (ValueError, json.JSONDecodeError):
                    after = []
                    model["graph_errors"].append(f"L{line}: {name}의 after를 해석하지 못함")
                model["tasks"].append({"name": name, "after": after, "line": line, "definition_line": line,
                                       "verification": field(table, row, "검증"),
                                       "oracle": field(table, row, "oracle"),
                                       "scope": field(table, row, "쓰기 범위")})
            elif "종결 조건" in section and "조건" in table["headers"]:
                condition = field(table, row, "조건")
                if condition:
                    model["evidence"].append({"condition": condition, "line": line,
                                              "verdict": field(table, row, "판정", "상태"),
                                              "method": field(table, row, "판정 수단", "판정 수단 (실행 가능한 것)", "검증 방법"),
                                              "evidence": field(table, row, "근거", "evidence")})
    names = [t["name"] for t in model["tasks"]]
    for name, count in Counter(names).items():
        if count > 1:
            model["graph_errors"].append(f"중복 작업 ID: {name}")
    for task in model["tasks"]:
        task.update(snapshots.get(task["name"], {"raw": "미기재 · 미검증", "evidence": "", "line": task["line"]}))
        task["state"] = state(task["raw"])
        for dep in task["after"]:
            if dep not in names:
                model["graph_errors"].append(f"{task['name']}: 선행 작업 {dep}가 없음")
    for name in snapshots.keys() - set(names):
        model["warnings"].append(f"상태만 있고 작업 분할에 없는 작업: {name}")
    for phase, item in model["phases"].items():
        if plain(item["raw"]).lower() not in ALIASES:
            model["warnings"].append(f"{phase}: 상태 원문 {item['raw'] or '(빈칸)'} · 미해석")
    if len(names) > 24:
        model["graph_errors"].append(f"작업 {len(names)}개: 가독성 상한 24개 초과 · 도식 생략, 보드와 원문 참조")
    return model


def graph_layers(model):
    remaining = {t["name"]: t for t in model["tasks"]}
    done, layers = set(), []
    while remaining:
        ready = [name for name, t in remaining.items() if set(t["after"]) <= done]
        if not ready:
            model["graph_errors"].append("순환 의존 또는 선행 누락: " + ", ".join(remaining))
            return []
        layers.append([remaining.pop(name) for name in ready])
        done.update(ready)
    return layers


def svg_start(title, description, width, height):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
            f'aria-label="{escape(title, quote=True)}"><title>{escape(title)}</title><desc>{escape(description)}</desc>'
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>')


def text(x, y, value, size=14, color="#193249"):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-family="Segoe UI,Malgun Gothic,sans-serif">{escape(value)}</text>'


def short_svg(value, units=28):
    """Conservative label width for mixed Korean/Latin text; title retains the full value."""
    used = 0
    for index, char in enumerate(value):
        used += 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used > units - 2:
            return value[:index] + '…'
    return value


def node(x, y, width, name, raw, key, line=None):
    label, symbol, fill, stroke = STATES[key]
    dash = ' stroke-dasharray="5 4"' if key == "unknown" else ""
    return (f'<g><title>{escape(name + " · 문서 상태: " + raw)}</title>'
            f'<rect data-node="true" x="{x}" y="{y}" width="{width}" height="86" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>'
            + text(x + 14, y + 26, short_svg(name), 14)
            + text(x + 14, y + 53, short_svg(f"{symbol} 문서: {raw or '미기재 · 미검증'}", 31), 12, stroke)
            + text(x + 14, y + 73, label + (f" · 원문 L{line}" if line else " · 상태 원문 없음"), 10, stroke) + '</g>')


def workflow_svg(model):
    parts = [svg_start("계획·구현·검증 흐름", "Current State 단계 현황의 원문이며 승인 유효성 미검증", 900, 158)]
    for i, phase in enumerate(("설계", "구현", "검증")):
        item = model["phases"].get(phase, {"raw": "미기재 · 미검증"})
        parts.append(node(12 + i * 300, 35, 250, phase, item["raw"], state(item["raw"]), item.get("line")))
        if i < 2:
            x = 268 + i * 300
            parts.append(f'<path d="M{x},77 h34 l-7,-5 m7,5 l-7,5" fill="none" stroke="#718596" stroke-width="2"/>')
    parts.append(text(12, 146, "사람 승인과 독립 검증을 생략하지 않습니다. 문서에 없는 상태는 ?로 남깁니다.", 12))
    return "".join(parts) + '</svg>'


MAX_DIAGRAM_WIDTH = 1000
NODE_W, NODE_H, ROW_PITCH, COL_PITCH = 250, 86, 130, 270


def vertical_dependencies_svg(model, layers, task_links=None):
    """Layer = row layout used when a horizontal flow would exceed MAX_DIAGRAM_WIDTH.

    A layer wider than the drawing wraps into several rows; every predecessor sits in an earlier
    layer, so it is always fully above its follower. Edges are orthogonal and run in the gaps.
    """
    layer_of = {task["name"]: i for i, group in enumerate(layers) for task in group}
    bypasses = [(dep, task["name"]) for task in model["tasks"] for dep in task["after"]
                if layer_of[task["name"]] - layer_of[dep] > 1]
    step = min(14, 300 // max(len(bypasses), 1))
    margin = 20 + step * len(bypasses)
    columns = max(1, (MAX_DIAGRAM_WIDTH - margin) // COL_PITCH)
    used = min(columns, max(len(group) for group in layers))
    width = max(600, margin + used * COL_PITCH)
    layer_top, top = [], 30
    for group in layers:
        layer_top.append(top)
        top += ((len(group) + columns - 1) // columns) * ROW_PITCH
    height = top - (ROW_PITCH - NODE_H) + 30
    positions, rows = {}, {}
    for i, group in enumerate(layers):
        rows[i] = (len(group) + columns - 1) // columns
        for j, task in enumerate(group):
            positions[task["name"]] = (margin + (j % columns) * COL_PITCH, layer_top[i] + (j // columns) * ROW_PITCH, j // columns)
    parts = [svg_start("작업 의존도", "after에 명시된 선행 관계이며 병렬 실행 판정이 아님", width, height)]
    for task in model["tasks"]:
        x, y, row = positions[task["name"]]
        for dep in task["after"]:
            px, py, prow = positions[dep]
            index = bypasses.index((dep, task["name"])) if (dep, task["name"]) in bypasses else -1
            bottom = layer_top[layer_of[dep]] + (rows[layer_of[dep]] - 1) * ROW_PITCH + NODE_H
            lane = bottom + 10 + (index % 2) * 8 if index >= 0 else bottom + 10
            if prow == rows[layer_of[dep]] - 1:
                route = f"M{px + NODE_W // 2},{py + NODE_H} V{lane}"
            else:
                route = f"M{px + NODE_W},{py + NODE_H // 2} H{px + NODE_W + 14} V{lane}"
            if index >= 0:
                gutter = 8 + index * step
                end_lane = layer_top[layer_of[task["name"]]] - 10 - (index % 2) * 8
                route += f" H{gutter} V{end_lane}"
            if row == 0:
                route += f" H{x + NODE_W // 2} V{y}"
                head = "l-5,-7 m5,7 l5,-7"
            else:
                route += f" H{x - 14} V{y + NODE_H // 2} H{x}"
                head = "l-7,-5 m7,5 l-7,5"
            route_type = "bypass" if index >= 0 else "adjacent"
            parts.append(f'<path data-from="{escape(dep, quote=True)}" data-to="{escape(task["name"], quote=True)}" data-route="{route_type}" d="{route} {head}" fill="none" stroke="#718596" stroke-width="2"><title>{escape(dep + " → " + task["name"] + " · 선행")}</title></path>')
    for task in model["tasks"]:
        x, y, _ = positions[task["name"]]
        markup = node(x, y, NODE_W, task["name"], task["raw"], task["state"], task["line"])
        if task_links:
            target = task_links[task["definition_line"]]
            markup = f'<a href="#{escape(target, quote=True)}" tabindex="0" aria-label="{escape(task["name"] + " 작업 상세로 이동", quote=True)}">{markup}</a>'
        parts.append(markup)
    return "".join(parts) + '</svg>'


def dependencies_svg(model, task_links=None):
    layers = graph_layers(model) if not model["graph_errors"] else []
    if not layers:
        note = " / ".join(model["graph_errors"]) or "작업 분할 표 없음 · 미검증"
        return svg_start("작업 의존도 미검증", note, 900, 80) + text(12, 38, note[:100], 13) + '</svg>'
    if len(layers) * 300 > MAX_DIAGRAM_WIDTH:
        return vertical_dependencies_svg(model, layers, task_links)
    columns = {task["name"]: i for i, group in enumerate(layers) for task in group}
    bypasses = [(dep, task["name"]) for task in model["tasks"] for dep in task["after"] if columns[task["name"]] - columns[dep] > 1]
    top = 40 + len(bypasses) * 14
    width, height = max(600, len(layers) * 300), (max(len(group) for group in layers) - 1) * 130 + top + 106
    parts = [svg_start("작업 의존도", "after에 명시된 선행 관계이며 병렬 실행 판정이 아님", width, height)]
    positions = {task["name"]: (20 + i * 300, top + j * 130) for i, group in enumerate(layers) for j, task in enumerate(group)}
    for task in model["tasks"]:
        x, y = positions[task["name"]]
        for dep in task["after"]:
            px, py = positions[dep]
            if (dep, task["name"]) in bypasses:
                route_y = 16 + bypasses.index((dep, task["name"])) * 14
                route = f"M{px + 250},{py + 43} H{px + 264} V{route_y} H{x - 14} V{y + 43} H{x}"
                route_type = "bypass"
            else:
                route = f"M{px + 250},{py + 43} C{px + 275},{py + 43} {x - 25},{y + 43} {x},{y + 43}"
                route_type = "adjacent"
            parts.append(f'<path data-from="{escape(dep, quote=True)}" data-to="{escape(task["name"], quote=True)}" data-route="{route_type}" d="{route} l-7,-5 m7,5 l-7,5" fill="none" stroke="#718596" stroke-width="2"><title>{escape(dep + " → " + task["name"] + " · 선행")}</title></path>')
    for task in model["tasks"]:
        x, y = positions[task["name"]]
        markup = node(x, y, 250, task["name"], task["raw"], task["state"], task["line"])
        if task_links:
            target = task_links[task["definition_line"]]
            markup = f'<a href="#{escape(target, quote=True)}" tabindex="0" aria-label="{escape(task["name"] + " 작업 상세로 이동", quote=True)}">{markup}</a>'
        parts.append(markup)
    return "".join(parts) + '</svg>'


def mermaid_label(value):
    # Encode all syntax/control characters; identifiers are generated, never user supplied.
    return "".join(c if c.isalnum() or c in " _-·가나다" else f"#{ord(c)};" for c in value.replace("\n", " "))


def diagram_sources(model, task_links=None):
    dependency_svg = dependencies_svg(model, task_links)
    workflow = ["flowchart LR"]
    for i, phase in enumerate(("설계", "구현", "검증")):
        raw = model["phases"].get(phase, {}).get("raw", "미기재 · 미검증")
        workflow.append(f'  P{i}["{mermaid_label(phase + " · " + STATES[state(raw)][0] + " · 문서: " + raw)}"]')
        workflow.append(f'  class P{i} {state(raw)}')
    workflow += ["  P0 -->|계획서| P1", "  P1 -->|결과와 독립 검증| P2"]
    graph = ["flowchart LR"]
    if model["graph_errors"] or not model["tasks"]:
        graph.append('  UNKNOWN["의존 관계 미검증 · 원문 확인"]')
    else:
        ids = {task["name"]: f"T{i}" for i, task in enumerate(model["tasks"])}
        for task in model["tasks"]:
            graph.append(f'  {ids[task["name"]]}["{mermaid_label(task["name"] + " · " + STATES[task["state"]][0] + " · 문서: " + task["raw"])}"]')
            graph.append(f'  class {ids[task["name"]]} {task["state"]}')
            graph.extend(f'  {ids[dep]} -->|선행| {ids[task["name"]]}' for dep in task["after"])
    for key, (_, _, fill, stroke) in STATES.items():
        rule = f'  classDef {key} fill:{fill},stroke:{stroke},color:{stroke}'
        workflow.append(rule)
        graph.append(rule)
    return {"workflow": (workflow_svg(model), "\n".join(workflow) + "\n"),
            "dependencies": (dependency_svg, "\n".join(graph) + "\n")}


def resume_fields(lines):
    result, current_level, fence = {}, None, None
    for number, line in enumerate(readable_lines(lines), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+)", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).lower()
            if "current state" in title or "현재 상태" in title:
                current_level = level
            elif current_level is not None and level <= current_level:
                current_level = None
        if current_level is not None:
            match = re.match(r"^\s*[-*]?\s*(현재 위치|확인된 사실|결정 필요|다음 한 단계)\s*[:：]\s*(.+)$", line)
            if match:
                key = match.group(1)
                result[key] = ("중복 원문 · 미검증", number) if key in result else (match.group(2), number)
    return result


def plan_namespace(source):
    return hashlib.sha256(source["name"].encode("utf-8")).hexdigest()[:12]


def task_anchor(source, task):
    return f'task-{plan_namespace(source)}-{task["definition_line"]}'


def prepare_view(source):
    """Discover graph errors before summaries; all HTML views share this model."""
    model = project_model(source)
    links = {task["definition_line"]: task_anchor(source, task) for task in model["tasks"]}
    return {"source": source, "model": model, "resume": resume_fields(source["lines"]),
            "diagrams": diagram_sources(model, links)}


def task_counts(model):
    counts = Counter(task["state"] for task in model["tasks"])
    return {key: counts[key] for key in STATES}


def render_views(source, model, diagrams=None):
    links = {task["definition_line"]: task_anchor(source, task) for task in model["tasks"]}
    diagrams = diagrams or diagram_sources(model, links)
    prefix = hashlib.sha256(source["name"].encode("utf-8")).hexdigest()[:12]
    parts = ['<div class="visual-note">문서 표기 시각화 · 승인/완료 유효성 미검증</div>',
             '<figure class="workflow" tabindex="0" aria-label="단계 흐름, 좁은 화면에서 가로 스크롤">' + diagrams["workflow"][0] + '<figcaption>계획 → 구현 → 검증. 상태는 Current State의 단계 현황 원문입니다.</figcaption></figure>']
    resume = resume_fields(source["lines"])
    parts.append('<div class="resume-grid">')
    for label in ("현재 위치", "다음 한 단계"):
        value, line = resume.get(label, ("미기재 · 미검증", None))
        parts.append(f'<section class="resume"><h3>{label}</h3><p>{escape(value)}</p><small>{"원문 L" + str(line) if line else "원문 없음"}</small></section>')
    parts.append('</div>')
    parts += ['<section class="flow-panel" aria-label="작업 흐름"><div class="section-heading"><h3>작업 흐름</h3><span>선행 → 후속</span></div>',
              '<figure class="dependency" tabindex="0" aria-label="작업 의존도, 필요한 경우 가로 스크롤">' + diagrams["dependencies"][0] + '<figcaption>화살표는 after 선행 관계입니다. 일정·병렬 허가·현재 차단 원인을 판정하지 않습니다. 노드에서 작업 상세로 이동한 뒤 Enter/Space로 펼칩니다.</figcaption></figure></section>']
    if model["graph_errors"]:
        parts.append('<aside class="warnings"><strong>작업 관계 미검증</strong><ul>' +
                     ''.join(f'<li>{escape(w)}</li>' for w in model["graph_errors"]) +
                     f'</ul><a href="#board-{prefix}">보존된 작업 목록 확인</a></aside>')
    if model["tasks"]:
        parts.append(f'<h3 id="board-{prefix}">작업 상태 보드 <span class="muted">제목을 눌러 근거 펼치기</span></h3><div class="task-board">')
        for key, (label, symbol, _, _) in STATES.items():
            tasks = [t for t in model["tasks"] if t["state"] == key]
            parts.append(f'<section class="lane {key}" id="lane-{prefix}-{key}" data-state="{key}" data-count="{len(tasks)}"><h4>{symbol} {label} <span>{len(tasks)}</span></h4>')
            for task in tasks:
                parts.append(f'<details class="task" id="{task_anchor(source, task)}"><summary><strong>{escape(task["name"])}</strong><span class="task-status">{symbol} {label}</span></summary>'
                             f'<small>상태 원문 L{task["line"]} · {escape(task["raw"])}</small>'
                             f'<p>선행: {escape(", ".join(task["after"]) or "없음")}</p><p>근거 원문: {escape(task["evidence"] or "미기재")}</p>'
                             f'<p>작업 정의 L{task["definition_line"]} · 쓰기 범위: {escape(task["scope"] or "미기재")}</p>'
                             f'<p>oracle: {escape(task["oracle"] or "미기재")}</p><p>검증 (계획): {escape(task["verification"] or "미기재")}</p></details>')
            parts.append('</section>')
        parts.append('</div>')
    else:
        parts.append('<p class="empty-state">작업 분할 표 없음 · 작업 상태 집계 미검증</p>')
    parts.append('<details class="evidence-panel"><summary>판단·검증 근거 펼치기</summary>')
    for label in ("결정 필요", "확인된 사실"):
        value, line = resume.get(label, ("미기재 · 미검증", None))
        parts.append(f'<h4>{label}</h4><p>{escape(value)}</p><small>{"원문 L" + str(line) if line else "원문 없음"}</small>')
    for phase, item in model["phases"].items():
        parts.append(f'<p class="phase-evidence">{escape(phase)} L{item["line"]} · 근거 원문: {escape(item["evidence"] or "미기재")}</p>')
    if model["evidence"]:
        parts.append('<h3>종결 조건과 검증 근거</h3><div class="table-scroll"><table><thead><tr><th>조건</th><th>문서 판정</th><th>판정 수단 (계획)</th><th>근거 원문</th><th>출처</th></tr></thead><tbody>')
        for item in model["evidence"]:
            values = [item["condition"], item["verdict"] or "미기재 · 미검증", item["method"] or "미기재", item["evidence"] or "미기재 · 증거 미검증", f'L{item["line"]}']
            parts.append('<tr>' + ''.join(f'<td>{escape(v)}</td>' for v in values) + '</tr>')
        parts.append('</tbody></table></div>')
    parts.append('</details>')
    warnings = model["warnings"]
    if warnings:
        parts.append('<aside class="warnings"><strong>미해석·미검증</strong><ul>' + ''.join(f'<li>{escape(w)}</li>' for w in warnings) + '</ul></aside>')
    parts.append(f'<details id="source-{prefix}"><summary>편집 가능한 Mermaid 도식 원본</summary><pre>{escape(diagrams["workflow"][1])}</pre><pre>{escape(diagrams["dependencies"][1])}</pre></details>')
    return ''.join(parts)


def concern_bars(counts):
    total = sum(counts.values())
    if not total:
        return '<p>상태 표기 집계 없음 · 미검증</p>'
    return '<figure class="concern-bars"><figcaption>우려 상태 표기 분포 · 위험 해소 판정이 아님</figcaption>' + ''.join(
        f'<div class="bar-row"><span>{escape(label)}</span><div class="bar-track"><div class="bar-fill" style="width:{count / total * 100:.2f}%"></div></div><strong>{count}</strong></div>'
        for label, count in sorted(counts.items())) + '</figure>'
