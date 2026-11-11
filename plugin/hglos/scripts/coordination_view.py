"""Read-only, escaped dashboard projection of an explicit coordination snapshot."""

from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path

KINDS = {'file': '파일', 'tree': '하위 폴더', 'resource': '공유 자원', 'workspace': '전체 작업 영역'}
ACTIVITY = {'observing': '○ 관측 신고', 'working': '▶ 작업 신고', 'waiting': '… 대기 신고',
            'blocked': '! 차단 신고', 'ended': '□ 종료 신고', 'recovered': '↩ 복구 기록'}
SESSION_CSS = '''
.session-metrics{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
.session-metric{padding:10px 14px;background:#f0f5f8;border-radius:8px;font-size:13px}
.session-metric strong{font-size:20px;margin-right:8px}
.session-card{border:1px solid #d6e0e8;border-radius:10px;padding:14px;min-width:0}
.session-card[open]{border-color:#17628e;background:#f9fcff}
.session-card>summary{font-size:14px;font-weight:600;overflow-wrap:anywhere;line-height:1.8}
.session-card dl{display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px;font-size:13px;line-height:1.8}
.session-card dt{font-weight:600}.session-card dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.session-card li,.scope-index li{font-size:13px;line-height:1.9;overflow-wrap:anywhere}
.session-card pre{margin-bottom:0}.session-map svg{display:block;width:100%;max-width:780px;min-width:560px;height:auto}
.scope-index>summary{line-height:1.8;overflow-wrap:anywhere}.session-map{overflow-x:auto}
summary:focus-visible,a:focus-visible{outline:3px solid #17628e;outline-offset:4px}
@media(max-width:600px){.session-card dl{grid-template-columns:1fr;gap:4px}.session-card dd{margin-bottom:8px}}
'''


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Snapshot timestamp must be a string')
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Snapshot timestamp must have a timezone')
    return parsed


def load_sessions(repo, name):
    path = (repo / name).resolve(strict=True)
    if not path.is_relative_to(repo) or path.suffix.lower() != '.json':
        raise ValueError('Session snapshot must be JSON inside repository')
    raw = path.read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError('Session snapshot too large')
    data = json.loads(raw.decode('utf-8'))
    if (not isinstance(data, dict) or type(data.get('schema')) is not int or data.get('schema') != 1
            or data.get('signature') != 'hglos-coordination-v1'
            or not isinstance(data.get('root'), str) or not data['root']
            or Path(data['root']).resolve() != repo):
        raise ValueError('Invalid/mismatched coordination snapshot')
    generated = timestamp(data.get('generated'))
    rows = data.get('sessions')
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Invalid session list')
    identifiers = set()
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ('id', 'host', 'task', 'state', 'last_seen', 'reason')):
            raise ValueError('Invalid session row')
        if not row['id'].strip() or row['id'] in identifiers:
            raise ValueError('Missing/duplicate session ID')
        identifiers.add(row['id'])
        timestamp(row['last_seen'])
        if 'version' in row and (type(row['version']) is not int or row['version'] < 1):
            raise ValueError('Invalid session version')
        if not isinstance(row.get('suspect'), bool) or not isinstance(row.get('claims'), list) or not isinstance(row.get('operations'), list):
            raise ValueError('Invalid session activity/claims')
        scopes, operations = set(), set()
        for claim in row['claims']:
            if (not isinstance(claim, dict) or not isinstance(claim.get('kind'), str)
                    or claim['kind'] not in KINDS or not isinstance(claim.get('scope'), str)
                    or not claim['scope'].strip() or claim.get('owner', row['id']) != row['id']):
                raise ValueError('Invalid claim/owner')
            key = (claim['kind'], claim['scope'])
            if key in scopes:
                raise ValueError('Duplicate claim')
            scopes.add(key)
        for operation in row['operations']:
            if (not isinstance(operation, dict)
                    or any(not isinstance(operation.get(k), str) for k in ('id', 'tool', 'started'))
                    or not operation['id'].strip() or operation.get('owner', row['id']) != row['id']):
                raise ValueError('Invalid operation/owner')
            timestamp(operation['started'])
            if operation['id'] in operations:
                raise ValueError('Duplicate operation')
            operations.add(operation['id'])
    data.update(path=path, sha256=hashlib.sha256(raw).hexdigest(),
                age_seconds=max(0, int((datetime.now(timezone.utc) - generated).total_seconds())))
    return data


def session_model(data):
    """One projection for cards, diagrams, reverse index, metrics and Markdown.

    Reasons are opaque reports. In particular, never reconstruct live blockers
    from a historical conflict string or treat a matching scope as permission.
    """
    rows = data['sessions']
    scopes = {}
    for index, row in enumerate(rows, 1):
        for claim in row['claims']:
            key = (claim['kind'], claim['scope'])
            scopes.setdefault(key, []).append({'id': row['id'], 'host': row['host'], 'index': index})
    return {'sessions': rows, 'scopes': scopes, 'totals': {
        'sessions': len(rows), 'claims': sum(len(r['claims']) for r in rows),
        'operations': sum(len(r['operations']) for r in rows),
        'waiting': sum(r['state'] in ('blocked', 'waiting') for r in rows),
        'suspect': sum(r['suspect'] for r in rows)}}


def activity(row):
    return ACTIVITY.get(row['state'], '? ' + row['state']) + (' / 연락 불명(suspect)' if row['suspect'] else '')


def session_cells(row):
    return [row['host'] + ' / ' + row['id'], row['task'] or '목적 미기록',
            activity(row),
            ', '.join(c['kind'] + ':' + c['scope'] for c in row['claims']) or '선점 없음',
            str(len(row['operations'])), row['reason'] or '-', row['last_seen']]


HEADERS = ['도구 / 세션', '작업 목적', '신고 상태', '선점 범위·자원', '미종료 호출', '마지막 신고 사유', '마지막 연락(UTC)']


def ownership_svg(row):
    """Only ownership recorded in this snapshot; no inferred wait-for edges."""
    claims = row['claims']
    if not claims or len(claims) > 12:
        return ''
    height = max(170, 55 + 64 * len(claims))
    center = height // 2
    label = row['host'] + ' / ' + row['id']

    def short(value, limit=24):
        return escape(value if len(value) <= limit else value[:limit - 1] + '…')

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 {height}" width="780" height="{height}" role="img" aria-label="세션 소유 관계">',
             '<title>세션 → 신고 선점 범위</title><desc>화살표는 수집 시점의 소유 관계입니다. 실행 순서나 현재 차단자를 뜻하지 않습니다. 전체 이름은 아래 목록에 있습니다.</desc>',
             f'<rect x="16" y="{center - 36}" width="245" height="72" rx="10" fill="#e7f1ff" stroke="#17628e"/>',
             f'<text x="30" y="{center - 5}" font-size="13" fill="#193249"><title>{escape(label)}</title>{short(label, 16)}</text>',
             f'<text x="30" y="{center + 20}" font-size="12" fill="#193249">{short(activity(row), 16)}</text>']
    for index, claim in enumerate(claims):
        y = 48 + index * 64
        kind, scope = claim['kind'], claim['scope']
        parts += [f'<path d="M261 {center} H300 V{y} H365 l-7,-4 m7,4 l-7,4" stroke="#718596" fill="none"/>',
                  f'<g><title>{escape(kind + ":" + scope)}</title><rect x="370" y="{y - 26}" width="390" height="52" rx="7" fill="#f0f5f8" stroke="#8295a6"/>',
                  f'<text x="384" y="{y - 6}" font-size="12" fill="#4e6175">{escape(KINDS[kind])} · {escape(kind)}</text>',
                  f'<text x="384" y="{y + 14}" font-size="13" fill="#193249">{short(scope)}</text></g>']
    return ''.join(parts) + '</svg>'


def session_detail(row, index):
    title = row['host'] + ' / ' + row['id']
    reason = row['reason'] or '사유 미기록 · 미검증'
    claims = ''.join(f'<li><code>{escape(c["kind"] + ":" + c["scope"])}</code> · {KINDS[c["kind"]]}</li>' for c in row['claims'])
    operations = ''.join('<li>' + escape(f'{o["tool"]} / {o["id"]} · 시작 {o["started"]}') + '</li>' for o in row['operations'])
    diagram = ownership_svg(row)
    graph = ('<figure class="session-map">' + diagram + '<figcaption>신고된 소유 관계 · 전체 이름은 선점 목록에 표시</figcaption></figure>') if diagram else (
        '<p class="visual-note">선점 12개 초과 · 도식 생략, 전체 선점 목록을 확인하세요.</p>' if len(row['claims']) > 12 else '')
    return (f'<details class="session-card" id="hglos-session-{index}"><summary>{escape(title)} · {escape(activity(row))} · '
            f'선점 {len(row["claims"])} · 미종료 호출 {len(row["operations"])} · 펼쳐 보기</summary>'
            f'<dl><dt>작업 목적</dt><dd>{escape(row["task"] or "목적 미기록")}</dd>'
            f'<dt>소유권 세대(version)</dt><dd>{row.get("version", "미기록")}</dd>'
            f'<dt>마지막 연락(UTC)</dt><dd>{escape(row["last_seen"])}</dd></dl>' + graph +
            '<h4>선점 범위·자원</h4><ul>' + (claims or '<li>선점 없음</li>') + '</ul>'
            '<h4>마지막 신고 사유</h4><pre>' + escape(reason) + '</pre>'
            '<p class="visual-note">과거 충돌 또는 대기·복구 신고일 수 있습니다. 현재 차단자·재시도 결과·해제 허가를 증명하지 않습니다.</p>'
            '<h4>미종료 호출</h4><ul>' + (operations or '<li>기록 없음 · 자원 해제 허가가 아님</li>') + '</ul></details>')


def sessions_html(data):
    if data is None:
        return ''
    model = session_model(data)
    rows = ''.join('<tr>' + ''.join('<td>' + escape(c) + '</td>' for c in session_cells(row)) + '</tr>'
                   for row in model['sessions'])
    metrics = ''.join(f'<div class="session-metric" data-metric="{key}" data-count="{value}"><strong>{value}</strong>{label}</div>'
                      for key, label in (('sessions', '세션'), ('claims', '선점 기록'), ('operations', '미종료 호출'),
                                         ('waiting', '대기·차단 신고'), ('suspect', '연락 불명'))
                      for value in (model['totals'][key],))
    scope_index = ''.join('<details class="scope-index"><summary>' + escape(kind + ':' + scope) +
                          f' · 소유 기록 {len(owners)}</summary><ul>' +
                          ''.join(f'<li><a href="#hglos-session-{o["index"]}">{escape(o["host"] + " / " + o["id"])}</a></li>' for o in owners) +
                          '</ul></details>' for (kind, scope), owners in sorted(model['scopes'].items()))
    return ('<article class="wide"><h2>세션 협업 현황 · 수집 시점 스냅샷</h2>'
            f'<p>보고서 생성 시 수집 후 {data["age_seconds"]}초 · 화면의 시간·상태는 자동 갱신되지 않습니다.</p>'
            f'<small>수집 {escape(data["generated"])} · SHA-256 {escape(data["sha256"])}</small>'
            '<p class="visual-note">참여 세션의 신고·관측 정보입니다. 오래된 선점은 자동 회수하지 않습니다. '
            '현재 화면은 실시간 갱신되지 않으며 구현 완료·PASS·승인·프로세스 생존을 증명하지 않습니다.</p>' +
            '<div class="session-metrics">' + metrics + '</div>'
            '<h3>세션별 소유 관계와 신고 사유</h3><p>세션 이름을 눌러 펼치세요. 키보드는 Tab으로 이동하고 Enter 또는 Space로 펼칠 수 있습니다.</p>' +
            (''.join(session_detail(row, i) for i, row in enumerate(model['sessions'], 1)) or '<p>등록된 세션 없음</p>') +
            '<h3>파일·자원별 소유 목록</h3><p>이름이 다른 범위끼리도 겹칠 수 있습니다. 이 목록은 병렬 작업 허가나 충돌 검사 결과가 아닙니다.</p>' +
            (scope_index or '<p>선점 기록 없음</p>') +
            '<details><summary>전체 세션 요약표</summary><div class="table-scroll"><table><thead><tr>'
            + ''.join('<th>' + escape(h) + '</th>' for h in HEADERS)
            + '</tr></thead><tbody>' + rows + '</tbody></table></div></details></article>')


def sessions_markdown(data):
    if data is None:
        return ''
    model = session_model(data)
    def cell(text):
        value = escape(text).replace('\\', '\\\\').replace('\n', ' ').replace('\r', ' ')
        for token in ('|', '`', '[', ']', '*', '_'):
            value = value.replace(token, '\\' + token)
        return value
    lines = ['## 세션 협업 현황 · 스냅샷',
             f"수집 {cell(data['generated'])} · 보고서 생성 시 수집 후 {data['age_seconds']}초 · SHA-256 {data['sha256']}",
             '참여 세션의 신고·관측 정보. 실시간 갱신/프로세스 생존/구현 완료/PASS/승인 증거가 아닙니다. '
             '사유는 마지막 신고이며 현재 차단자·재시도 결과·해제 허가를 증명하지 않습니다.',
             '집계: ' + ' · '.join(f'{key}={value}' for key, value in model['totals'].items()),
             '| ' + ' | '.join(HEADERS) + ' |', '| ' + ' | '.join(['---'] * len(HEADERS)) + ' |']
    lines += ['| ' + ' | '.join(cell(c) for c in session_cells(row)) + ' |' for row in model['sessions']]
    result = '\n\n'.join(lines[:4]) + '\n\n' + '\n'.join(lines[4:])
    result += '\n\n### 파일·자원별 소유 목록\n\n동일 이름별 목록이며 범위 겹침·병렬 허가를 판정하지 않습니다.\n\n'
    result += '\n'.join('- ' + cell(kind + ':' + scope) + ' → ' + ', '.join(cell(o['host'] + ' / ' + o['id']) for o in owners)
                        for (kind, scope), owners in sorted(model['scopes'].items())) or '선점 기록 없음'
    for row in model['sessions']:
        result += '\n\n#### ' + cell(row['host'] + ' / ' + row['id']) + ' · 미종료 호출\n\n'
        result += '\n'.join('- ' + cell(f'{o["tool"]} / {o["id"]} · 시작 {o["started"]}') for o in row['operations']) or '기록 없음 · 자원 해제 허가가 아님'
    return result + '\n'
