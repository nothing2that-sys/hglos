#!/usr/bin/env python3
"""Local cooperative session coordination. This is not a filesystem sandbox."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

SCHEMA = 1  # coordination.json and snapshot format
DB_SCHEMA = 2  # 2: call-scoped claims (claims.call) and per-session context fingerprint
MARKER = Path('.hglos/coordination.json')
RUNTIME = Path('.hglos/runtime')
SIGNATURE = 'hglos-coordination-v1'


class CoordinationError(ValueError):
    pass


class Conflict(CoordinationError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__('Claim conflict: ' + json.dumps(conflicts, ensure_ascii=False))


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def no_links(path, stop=None):
    """Reject reparse points; callers first resolve the explicitly selected root."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if stop is not None and item == stop:
            break
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise CoordinationError(f'Linked/reparse path is unsupported: {item}')
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise CoordinationError(f'Hard-linked metadata/path is unsupported: {item}')
    return path


def root_identity(root):
    info = root.stat()
    if not info.st_ino:
        raise CoordinationError('Filesystem does not expose stable directory identity')
    return f'{info.st_dev}:{info.st_ino}'


def find_root(path):
    current = Path(path).resolve(strict=True)
    if not current.is_dir():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / MARKER).exists():
            return candidate
    raise CoordinationError('Coordination is not initialized. Run coordination.py --repo ROOT init')


def baseline(root):
    def git(*args):
        try:
            r = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    top = git('rev-parse', '--show-toplevel')
    if top is None:
        # A .git marker without a readable Git baseline must not look like a plain folder.
        if any((p / '.git').exists() for p in (root, *root.parents)):
            raise CoordinationError('Git workspace detected but baseline is unreadable')
        return None
    return {'top': os.path.normcase(str(Path(top).resolve())),
            'head': git('rev-parse', '--verify', 'HEAD'),
            'branch': git('symbolic-ref', '--quiet', 'HEAD')}


def initialize(path, mode='exclusive'):
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise CoordinationError('Root must be a directory')
    for parent in root.parents:
        if (parent / MARKER).exists():
            raise CoordinationError(f'Use existing ancestor coordinator: {parent}')
    folder = no_links(root / '.hglos', root)
    folder.mkdir(exist_ok=True)
    marker = no_links(root / MARKER, root)
    config = {'schema': SCHEMA, 'mode': mode, 'root_id': root_identity(root)}
    if marker.exists():
        old = json.loads(marker.read_text(encoding='utf-8'))
        if old != config:
            raise CoordinationError('Existing coordination config differs; live mode changes are unsupported')
    else:
        with marker.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(config, indent=2) + '\n')
    ignore = folder / '.gitignore'
    if not ignore.exists():
        ignore.write_text('runtime/\ncoordination.json\n', encoding='utf-8')
    elif 'runtime/' not in ignore.read_text(encoding='utf-8').splitlines():
        raise CoordinationError('Add runtime/ to .hglos/.gitignore before storing local session data')
    with Registry(root):
        pass
    return config


class Registry:
    def __init__(self, path):
        self.root = find_root(path)
        no_links(self.root / MARKER, self.root)
        self.config = json.loads((self.root / MARKER).read_text(encoding='utf-8'))
        if self.config.get('schema') != SCHEMA or self.config.get('mode') not in ('exclusive', 'scoped'):
            raise CoordinationError('Unsupported coordination configuration')
        if self.config.get('root_id') != root_identity(self.root):
            raise CoordinationError('Workspace identity changed; copied coordination config must be reinitialized')
        runtime = no_links(self.root / RUNTIME, self.root)
        runtime.mkdir(exist_ok=True)
        self.dbpath = no_links(runtime / 'coordination.sqlite3', self.root)
        for suffix in ('-journal', '-wal', '-shm'):
            no_links(Path(str(self.dbpath) + suffix), self.root)
        self.db = sqlite3.connect(self.dbpath, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('PRAGMA synchronous = FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, DB_SCHEMA):
            self.db.close()
            raise CoordinationError('Unsupported coordination database version')
        if version == 0:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, host TEXT NOT NULL, task TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, version INTEGER NOT NULL,
                baseline TEXT NOT NULL, started TEXT NOT NULL, seen REAL NOT NULL,
                notice TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS claims (
                owner TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL,
                scope TEXT NOT NULL, call TEXT NOT NULL DEFAULT '', UNIQUE(owner,kind,scope,call));
            CREATE TABLE IF NOT EXISTS operations (
                owner TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL,
                tool TEXT NOT NULL, started TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', UNIQUE(owner,id));
            CREATE TABLE IF NOT EXISTS handoffs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id),
                artifact TEXT NOT NULL, sha256 TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, event TEXT NOT NULL,
                detail TEXT NOT NULL, created TEXT NOT NULL);
            ''')
            self.db.execute(f'PRAGMA user_version = {DB_SCHEMA}')
        elif version == 1:
            self.migrate_from_1()

    def migrate_from_1(self):
        """Schema 1 claims are persistent: copy them with an empty call ID, lose nothing."""
        with self.transaction():
            if self.db.execute('PRAGMA user_version').fetchone()[0] != 1:
                return  # Another process migrated first.
            self.db.execute('''CREATE TABLE claims_v2 (
                owner TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL,
                scope TEXT NOT NULL, call TEXT NOT NULL DEFAULT '', UNIQUE(owner,kind,scope,call))''')
            self.db.execute("INSERT INTO claims_v2(owner,kind,scope,call) SELECT owner,kind,scope,'' FROM claims")
            self.db.execute('DROP TABLE claims')
            self.db.execute('ALTER TABLE claims_v2 RENAME TO claims')
            self.db.execute("ALTER TABLE sessions ADD COLUMN notice TEXT NOT NULL DEFAULT ''")
            self.db.execute("ALTER TABLE operations ADD COLUMN agent TEXT NOT NULL DEFAULT ''")
            self.event(None, 'migrated', 'registry schema 1 -> 2')
            self.db.execute(f'PRAGMA user_version = {DB_SCHEMA}')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def event(self, owner, event, detail=''):
        self.db.execute('INSERT INTO events(owner,event,detail,created) VALUES (?,?,?,?)',
                        (owner, event, str(detail)[:4000], stamp()))

    def session(self, sid, version=None, check_baseline=False):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None or row['state'] in ('ended', 'recovered'):
            raise CoordinationError('Unknown or ended/recovered session; use a new session ID')
        if version is not None and version != row['version']:
            raise CoordinationError('Stale session version')
        if check_baseline and not self.follow_baseline(row):
            raise CoordinationError(
                'Git branch changed or HEAD was rewritten; writes stopped (reads continue). After confirming '
                f'quiescence run: coordination.py --repo ROOT rebaseline --session {sid} --version {row["version"]} --quiescent')
        return row

    def follow_baseline(self, row):
        """True if the baseline is unchanged or advanced by descendant commits on the same branch.

        An advance is adopted and audited; branch switches and rewinds/rewrites are not.
        Callers hold a write transaction when the update must persist.
        """
        stored, current = json.loads(row['baseline']), baseline(self.root)
        if stored == current:
            return True
        if not (isinstance(stored, dict) and isinstance(current, dict) and current.get('head')
                and stored.get('branch') and stored.get('top') == current.get('top')
                and stored.get('branch') == current.get('branch')):
            return False
        if stored.get('head') is not None:
            try:
                check = subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor',
                                        stored['head'], current['head']], capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return False
            if check.returncode != 0:
                return False
        self.db.execute('UPDATE sessions SET baseline=? WHERE id=?', (json.dumps(current, sort_keys=True), row['id']))
        self.event(row['id'], 'baseline-advanced',
                   json.dumps({'from': stored.get('head'), 'to': current['head'], 'branch': current['branch']}))
        return True

    def rebaseline(self, sid, version, quiescent=False):
        """Adopt the current branch/HEAD for the same session after its writers stopped."""
        if not quiescent:
            raise CoordinationError('Rebaseline requires --quiescent after all child writers have stopped')
        current = baseline(self.root)
        with self.transaction():
            row = self.session(sid, version)
            if self.db.execute('SELECT 1 FROM operations WHERE owner=?', (sid,)).fetchone():
                raise CoordinationError('Outstanding tool operations; cannot rebaseline')
            self.db.execute('UPDATE sessions SET baseline=?,version=version+1,seen=? WHERE id=?',
                            (json.dumps(current, sort_keys=True), time.time(), sid))
            self.event(sid, 'rebaselined', json.dumps({'from': json.loads(row['baseline']), 'to': current}))
        return {'session': sid, 'version': version + 1, 'baseline': current}

    def register(self, host, task='', sid=None, resume=False):
        sid = sid or str(uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', sid):
            raise CoordinationError('Invalid session ID')
        if not host or len(host) > 80 or len(task) > 2000:
            raise CoordinationError('Invalid host/task')
        current = baseline(self.root)
        with self.transaction():
            old = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
            if old:
                if old['host'] != host:
                    raise CoordinationError('Session host mismatch')
                if old['state'] == 'ended' and resume:
                    if (self.db.execute('SELECT 1 FROM claims WHERE owner=?', (sid,)).fetchone()
                            or self.db.execute('SELECT 1 FROM operations WHERE owner=?', (sid,)).fetchone()):
                        raise CoordinationError('Ended session retains claims/operations; confirm quiescence and recover first')
                    self.db.execute("UPDATE sessions SET state='observing',reason='',version=version+1,baseline=?,started=?,seen=?,task=? WHERE id=?",
                                    (json.dumps(current, sort_keys=True), stamp(), time.time(), task or old['task'], sid))
                    self.event(sid, 'resumed', 'Clean ended session; new version')
                else:
                    # Observation/control stays available; writes re-check the baseline.
                    self.follow_baseline(self.session(sid))
                    self.db.execute('UPDATE sessions SET seen=?,task=? WHERE id=?',
                                    (time.time(), task or old['task'], sid))
            else:
                self.db.execute('INSERT INTO sessions(id,host,task,state,reason,version,baseline,started,seen) '
                                'VALUES (?,?,?,?,?,?,?,?,?)',
                                (sid, host, task, 'observing', '', 1,
                                 json.dumps(current, sort_keys=True), stamp(), time.time()))
                self.event(sid, 'registered')
        return dict(self.session(sid))

    def normalize(self, kind, value):
        if kind == 'workspace':
            return '.'
        if kind == 'resource':
            if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,150}', value):
                raise CoordinationError('Invalid resource name')
            return value.lower()
        if kind not in ('file', 'tree') or any(c in value for c in '*?[]\x00'):
            raise CoordinationError('Only exact files and explicit subtrees are supported')
        candidate = Path(value)
        # Reject Win32 aliases/ADS/devices even on non-Windows hosts: portable claim names.
        for part in candidate.parts:
            if part in (candidate.anchor, '.', '..'):
                continue
            if part.endswith(('.', ' ')) or ':' in part or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I):
                raise CoordinationError('Non-canonical Windows filename/stream/device is unsupported')
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = Path(os.path.abspath(candidate))
        if not candidate.is_relative_to(self.root):
            raise CoordinationError('Claim escapes workspace')
        no_links(candidate, self.root)
        # Resolve existing components (including Windows short names) only AFTER link rejection.
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.root):
            raise CoordinationError('Resolved claim escapes workspace')
        relative = candidate.relative_to(self.root)
        if any(part.lower() in ('.git', '.hglos', '.codex', '.claude', '.agents') for part in relative.parts):
            raise CoordinationError('Coordination/host/Git metadata cannot be a file claim')
        if candidate.exists():
            if kind == 'file' and (not candidate.is_file() or candidate.stat().st_nlink > 1):
                raise CoordinationError('Expected a regular, singly-linked file')
            if kind == 'tree' and not candidate.is_dir():
                raise CoordinationError('Expected a directory subtree')
        # Case folding is conservative on case-sensitive filesystems, never more permissive.
        return relative.as_posix().casefold() or '.'

    @staticmethod
    def overlaps(left, right):
        lk, lp = left
        rk, rp = right
        if lk == 'workspace' or rk == 'workspace':
            return True
        if lk == 'resource' or rk == 'resource':
            return lk == rk and lp == rp
        return (lp == rp or (lk == 'tree' and (lp == '.' or rp.startswith(lp + '/')))
                or (rk == 'tree' and (rp == '.' or lp.startswith(rp + '/'))))

    def _claim(self, sid, scopes, version=None, call=''):
        self.session(sid, version, check_baseline=True)
        if self.config['mode'] == 'exclusive' and any(k != 'workspace' for k, _ in scopes):
            raise CoordinationError('Exclusive mode requires a whole-workspace claim')
        conflicts = []
        for row in self.db.execute('SELECT owner,kind,scope FROM claims WHERE owner<>?', (sid,)):
            if any(self.overlaps(scope, (row['kind'], row['scope'])) for scope in scopes):
                conflicts.append(dict(row))
        if conflicts:
            raise Conflict(conflicts)
        self.db.executemany('INSERT OR IGNORE INTO claims(owner,kind,scope,call) VALUES (?,?,?,?)',
                            [(sid, kind, scope, call) for kind, scope in scopes])
        self.db.execute("UPDATE sessions SET state='working',reason='',seen=? WHERE id=?", (time.time(), sid))
        self.event(sid, 'claim', json.dumps(scopes))

    def claim(self, sid, scopes, version=None):
        normalized = sorted(set((kind, self.normalize(kind, value)) for kind, value in scopes))
        if not normalized:
            normalized = [('workspace', '.')]
        try:
            with self.transaction():
                observed_version = self.session(sid, version)['version']
                self._claim(sid, normalized, version)
        except Conflict as exc:
            self.record_conflict(sid, observed_version, exc)
            raise
        return normalized

    def record_conflict(self, sid, version, error):
        # Recovery/end/release may have won AFTER the failed allocation rolled back.
        # Never resurrect a terminal owner or overwrite a new ownership generation.
        with self.transaction():
            changed = self.db.execute("UPDATE sessions SET state='blocked',reason=?,seen=? "
                                      "WHERE id=? AND version=? AND state NOT IN ('ended','recovered')",
                                      (str(error)[:4000], time.time(), sid, version)).rowcount
            self.event(sid, 'conflict' if changed else 'late-conflict', error)

    def heartbeat(self, sid, state='observing', reason='', version=None):
        if state not in ('observing', 'working', 'waiting', 'blocked'):
            raise CoordinationError('Invalid session activity state')
        with self.transaction():
            self.session(sid, version)
            self.db.execute('UPDATE sessions SET seen=?,state=?,reason=? WHERE id=?',
                            (time.time(), state, reason[:4000], sid))

    def before(self, sid, operation, tool, scopes, call_scoped=False, agent=''):
        """Track a tool call. call_scoped claims end with the call (Post or end-of-turn cleanup).

        agent: the host's sub-agent ID ('' = main agent) so one agent's turn end never settles another's calls.
        """
        if not operation or len(operation) > 200:
            raise CoordinationError('Missing/invalid tool_use_id; cannot track operation lifetime')
        normalized = sorted(set((kind, self.normalize(kind, value)) for kind, value in scopes))
        try:
            with self.transaction():
                observed_version = self.session(sid)['version']
                self._claim(sid, normalized, call=operation if call_scoped else '')
                self.db.execute('INSERT OR IGNORE INTO operations(owner,id,tool,started,agent) VALUES (?,?,?,?,?)',
                                (sid, operation, tool[:100], stamp(), agent[:200]))
        except Conflict as exc:
            self.record_conflict(sid, observed_version, exc)
            raise

    def after(self, sid, operation):
        with self.transaction():
            self.session(sid)
            self.db.execute('DELETE FROM operations WHERE owner=? AND id=?', (sid, operation))
            self.db.execute("DELETE FROM claims WHERE owner=? AND call=? AND call<>''", (sid, operation))
            self.db.execute('UPDATE sessions SET seen=? WHERE id=?', (time.time(), sid))

    def settle(self, sid, reason, agent=''):
        """End-of-turn cleanup for ONE agent: its open calls have ended; drop them and their call-scoped claims.

        Calls of other agents sharing this session ID (e.g. a running parent shell) are left untouched.
        """
        with self.transaction():
            self.session(sid)
            abandoned = [dict(r) for r in self.db.execute(
                'SELECT id,tool FROM operations WHERE owner=? AND agent=? ORDER BY id', (sid, agent))]
            calls = 0
            for item in abandoned:
                calls += self.db.execute('DELETE FROM claims WHERE owner=? AND call=?', (sid, item['id'])).rowcount
                self.db.execute('DELETE FROM operations WHERE owner=? AND id=?', (sid, item['id']))
            if abandoned:
                self.event(sid, 'abandoned-operations', json.dumps({'at': reason, 'agent': agent,
                                                                    'operations': abandoned, 'call_claims': calls}))
        return abandoned

    def set_notice(self, sid, fingerprint):
        with self.transaction():
            self.db.execute('UPDATE sessions SET notice=? WHERE id=?', (fingerprint, sid))

    def release(self, sid, version, quiescent=False):
        if not quiescent:
            raise CoordinationError('Release requires --quiescent after all child writers have stopped')
        with self.transaction():
            self.session(sid, version)
            if self.db.execute('SELECT 1 FROM operations WHERE owner=?', (sid,)).fetchone():
                raise CoordinationError('Outstanding tool operations; cannot release')
            self.db.execute('DELETE FROM claims WHERE owner=?', (sid,))
            self.db.execute("UPDATE sessions SET state='observing',reason='',seen=?,version=version+1 WHERE id=?", (time.time(), sid))
            self.event(sid, 'released')
        return {'session': sid, 'version': version + 1, 'released': True}

    def end(self, sid, version=None):
        with self.transaction():
            self.session(sid, version)
            self.db.execute("UPDATE sessions SET state='ended',seen=? WHERE id=?", (time.time(), sid))
            self.event(sid, 'ended', 'Claims retained; explicit quiescent recovery if any remain')

    def recover(self, sid, version, reason, quiescent=False):
        if not quiescent or not reason.strip():
            raise CoordinationError('Recovery requires --quiescent and an audit reason')
        with self.transaction():
            row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
            if row is None or row['version'] != version or row['state'] == 'recovered':
                raise CoordinationError('Unknown/stale/already recovered session')
            self.db.execute('DELETE FROM claims WHERE owner=?', (sid,))
            self.db.execute('DELETE FROM operations WHERE owner=?', (sid,))
            self.db.execute("UPDATE sessions SET state='recovered',version=version+1,reason=? WHERE id=?",
                            (reason[:4000], sid))
            self.event(sid, 'recovered', reason)

    def handoff(self, sid, artifact, note):
        self.session(sid, check_baseline=True)
        self.normalize('file', artifact)
        path = self.root / artifact
        if not path.is_file():
            raise CoordinationError('Handoff artifact must exist')
        raw = path.read_bytes()
        record = {'id': str(uuid.uuid4()), 'owner': sid,
                  'artifact': path.relative_to(self.root).as_posix(),
                  'sha256': hashlib.sha256(raw).hexdigest(), 'note': note[:2000], 'created': stamp()}
        with self.transaction():
            self.session(sid)
            self.db.execute('INSERT INTO handoffs VALUES (:id,:owner,:artifact,:sha256,:note,:created)', record)
            self.event(sid, 'handoff', record['id'])
        return record

    def verify_handoff(self, identifier):
        row = self.db.execute('SELECT * FROM handoffs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise CoordinationError('Unknown handoff')
        path = self.root / row['artifact']
        self.normalize('file', row['artifact'])
        matches = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        return dict(row, matches_current_bytes=matches, verification='hash-only; no correctness/approval claim')

    def snapshot(self, stale_after=180):
        if stale_after < 1:
            raise CoordinationError('stale-after must be positive')
        self.db.execute('BEGIN')
        try:
            rows = [dict(row) for row in self.db.execute('SELECT * FROM sessions ORDER BY started,id')]
            # One row per owner/kind/scope; a persistent claim ('') wins over a call-scoped duplicate.
            claims = [dict(row) for row in self.db.execute(
                'SELECT owner,kind,scope,MIN(call) AS call FROM claims GROUP BY owner,kind,scope ORDER BY owner,kind,scope')]
            ops = [dict(row) for row in self.db.execute('SELECT * FROM operations ORDER BY owner,id')]
            for row in rows:
                row.pop('notice', None)
                row['last_seen'] = datetime.fromtimestamp(row.pop('seen'), timezone.utc).isoformat(timespec='seconds')
                row['suspect'] = time.time() - datetime.fromisoformat(row['last_seen']).timestamp() > stale_after
                row['baseline'] = json.loads(row['baseline'])
                row['claims'] = [c for c in claims if c['owner'] == row['id']]
                row['operations'] = [o for o in ops if o['owner'] == row['id']]
            return {'schema': SCHEMA, 'signature': SIGNATURE, 'root': str(self.root),
                    'root_id': root_identity(self.root), 'mode': self.config['mode'],
                    'generated': stamp(), 'stale_after': stale_after, 'sessions': rows,
                    'handoffs': [dict(r) for r in self.db.execute('SELECT * FROM handoffs ORDER BY created')],
                    'events': [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 40')],
                    'limitations': 'Cooperative participants only. Suspect never expires claims. No project completion/approval inference.'}
        finally:
            self.db.execute('COMMIT')


def print_status(snapshot):
    print(f"HGLOS sessions | {snapshot['root']} | {snapshot['mode']} | {snapshot['generated']}")
    for item in snapshot['sessions']:
        scopes = ', '.join(c['kind'] + ':' + c['scope'] for c in item['claims']) or '-'
        print(f"{item['id']} v{item['version']} | {item['host']} | {item['state']}"
              f"{' / SUSPECT' if item['suspect'] else ''} | {item['task']} | {scopes}")
        if item['reason']:
            print('  ' + item['reason'])


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    init.add_argument('--mode', choices=('exclusive', 'scoped'), default='exclusive')
    reg = sub.add_parser('register')
    reg.add_argument('--host', required=True)
    reg.add_argument('--task', default='')
    reg.add_argument('--session')
    for name in ('claim', 'heartbeat', 'release', 'rebaseline', 'end', 'recover', 'handoff'):
        p = sub.add_parser(name)
        p.add_argument('--session', required=True)
        p.add_argument('--version', required=True, type=int)
        if name == 'claim':
            p.add_argument('--write', action='append', default=[])
            p.add_argument('--tree', action='append', default=[])
            p.add_argument('--resource', action='append', default=[])
        elif name == 'heartbeat':
            p.add_argument('--state', choices=('observing', 'working', 'waiting', 'blocked'), default='observing')
            p.add_argument('--reason', default='')
        elif name in ('release', 'rebaseline', 'recover'):
            p.add_argument('--quiescent', action='store_true')
            if name == 'recover':
                p.add_argument('--reason', required=True)
        elif name == 'handoff':
            p.add_argument('--artifact', required=True)
            p.add_argument('--note', default='')
    verify = sub.add_parser('verify-handoff')
    verify.add_argument('--id', required=True)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true')
    status.add_argument('--watch', action='store_true')
    status.add_argument('--interval', type=float, default=3)
    status.add_argument('--count', type=int, default=0)
    status.add_argument('--stale-after', type=int, default=180)
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('--stale-after', type=int, default=180)
    args = parser.parse_args()
    try:
        if args.action == 'init':
            result = initialize(args.repo, args.mode)
        else:
            with Registry(args.repo) as registry:
                if args.action == 'register':
                    result = registry.register(args.host, args.task, args.session)
                elif args.action == 'claim':
                    scopes = [('file', p) for p in args.write] + [('tree', p) for p in args.tree] + [('resource', p) for p in args.resource]
                    result = registry.claim(args.session, scopes, args.version)
                elif args.action == 'heartbeat':
                    result = registry.heartbeat(args.session, args.state, args.reason, args.version)
                elif args.action == 'release':
                    result = registry.release(args.session, args.version, args.quiescent)
                elif args.action == 'rebaseline':
                    result = registry.rebaseline(args.session, args.version, args.quiescent)
                elif args.action == 'end':
                    result = registry.end(args.session, args.version)
                elif args.action == 'recover':
                    result = registry.recover(args.session, args.version, args.reason, args.quiescent)
                elif args.action == 'handoff':
                    registry.session(args.session, args.version)
                    result = registry.handoff(args.session, args.artifact, args.note)
                elif args.action == 'verify-handoff':
                    result = registry.verify_handoff(args.id)
                    if not result['matches_current_bytes']:
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                        return 3
                else:
                    if args.action == 'status' and (args.interval < .2 or args.count < 0):
                        raise CoordinationError('Invalid watch interval/count')
                    count = 0
                    while True:
                        result = registry.snapshot(args.stale_after)
                        if args.action == 'snapshot':
                            output = no_links(registry.root / RUNTIME / 'sessions.json', registry.root)
                            temp = output.with_name('sessions.' + uuid.uuid4().hex + '.tmp')
                            try:
                                temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                                os.replace(temp, output)
                            finally:
                                temp.unlink(missing_ok=True)
                            print(output)
                            return 0
                        if args.json:
                            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                        else:
                            print_status(result)
                        count += 1
                        if not args.watch or (args.count and count >= args.count):
                            return 0
                        time.sleep(args.interval)
        print(json.dumps(result if result is not None else {'ok': True}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
