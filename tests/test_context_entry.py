"""doc/31 O oracle candidate (not frozen until JH confirms).

Wire contract fixed here: UTF-8 stdout contains one JSON object, stderr is empty.
Index: {schema_version:1, derived:true, root:absolute physical path, layout,
task, files:[{path:relative POSIX path, sha256:lowercase hex}]}.
Routes: {schema_version:1, tasks:{task:[relative path,...]}}. Common files are
implicit mandatory inputs, never suppressible by routes. Check succeeds with
the index fields plus content_match, index_sha256, repo and attestation limits.
Failure (exit 1 mismatch, 2 invalid/input/query): {error:{code,message}}, never
a successful receipt. Error codes are nonempty strings, wording is unrestricted.
Git receipt repo fields: root, branch (null detached), head (null unborn),
unborn, dirty. Missing Git metadata is an error, never a fabricated receipt.
"""
import contextlib
import builtins
import copy
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / 'plugin/hglos/scripts/context_index.py'
TASKS = ('answer', 'design', 'implement', 'verify', 'dashboard', 'coordinate')
COMMON = ('ENTRY.md', 'context_routes.json', 'RULES.md', 'HOST.md', 'COORDINATION.md')
POPEN_SIGNATURE = inspect.signature(subprocess.Popen)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return env


@contextlib.contextmanager
def observe_sentinel_reads(sentinel, *aliases):
    """Observe standard Python content-open APIs; no OS access-control claim.

    The sentinel is harmless fixture content. Record attempted reads even if the
    consumer later catches an error or returns the expected input-error code.
    """
    targets = {os.path.normcase(os.path.abspath(p)) for p in (sentinel, *aliases)}
    attempts = []

    def is_target(path):
        return not isinstance(path, int) and os.path.normcase(os.path.abspath(os.fsdecode(path))) in targets

    def wrap(original, name, flags=False):
        def opened(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get('flags' if flags else 'mode', 0 if flags else 'r')
            reading = (mode & (os.O_WRONLY | os.O_RDWR)) != os.O_WRONLY if flags else ('r' in mode or '+' in mode)
            if reading and is_target(path):
                attempts.append(dict(api=name, path=os.path.normcase(os.path.abspath(os.fsdecode(path)))))
            return original(path, *args, **kwargs)
        return opened

    with patch.object(builtins, 'open', wrap(builtins.open, 'builtins.open')), \
         patch.object(io, 'open', wrap(io.open, 'io.open')), \
         patch.object(os, 'open', wrap(os.open, 'os.open', flags=True)):
        yield attempts


@contextlib.contextmanager
def simulated_link(path, target=None, *, kind='symlink'):
    """One coherent link fixture for metadata and standard link predicates.

    Python 3.14 Windows predicates can bypass os.stat/lstat through nt helpers.
    Keep those predicates consistent too; this is not a real filesystem link.
    """
    path = Path(path)
    identity = os.path.normcase(os.path.abspath(path))
    link_target = Path(target) if target is not None else path
    assert kind in ('symlink', 'junction')
    original_stat, original_lstat = os.stat, os.lstat
    original_islink, original_path_islink = os.path.islink, Path.is_symlink
    info = original_lstat(path)
    fake = SimpleNamespace(**{k: getattr(info, k) for k in dir(info) if k.startswith('st_')})
    fake.st_mode = (stat.S_IFLNK | 0o777) if kind == 'symlink' else (stat.S_IFDIR | 0o755)
    fake.st_file_attributes = getattr(info, 'st_file_attributes', 0) | stat.FILE_ATTRIBUTE_REPARSE_POINT
    fake.st_reparse_tag = stat.IO_REPARSE_TAG_SYMLINK if kind == 'symlink' else stat.IO_REPARSE_TAG_MOUNT_POINT

    def selected(p):
        return not isinstance(p, int) and os.path.normcase(os.path.abspath(os.fsdecode(p))) == identity

    def lstat(p, *args, **kwargs):
        return fake if selected(p) else original_lstat(p, *args, **kwargs)

    def stat_call(p, *args, **kwargs):
        return fake if not kwargs.get('follow_symlinks', True) and selected(p) else original_stat(p, *args, **kwargs)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(os, 'lstat', lstat))
        stack.enter_context(patch.object(os, 'stat', stat_call))
        stack.enter_context(patch.object(os.path, 'islink', lambda p: kind == 'symlink' if selected(p) else original_islink(p)))
        stack.enter_context(patch.object(Path, 'is_symlink', lambda p: kind == 'symlink' if selected(p) else original_path_islink(p)))
        if hasattr(os.path, 'isjunction'):
            original_isjunction = os.path.isjunction
            stack.enter_context(patch.object(os.path, 'isjunction', lambda p: kind == 'junction' if selected(p) else original_isjunction(p)))
        if hasattr(Path, 'is_junction'):
            original_path_isjunction = Path.is_junction
            stack.enter_context(patch.object(Path, 'is_junction', lambda p: kind == 'junction' if selected(p) else original_path_isjunction(p)))
        original_readlink, original_path_readlink = os.readlink, Path.readlink
        stack.enter_context(patch.object(os, 'readlink', lambda p, *a, **kw:
            (os.fsencode(link_target) if isinstance(p, bytes) else str(link_target)) if selected(p)
            else original_readlink(p, *a, **kw)))
        stack.enter_context(patch.object(Path, 'readlink', lambda p: link_target if selected(p) else original_path_readlink(p)))
        if os.name == 'nt':
            import nt
            native = getattr(nt, '_path_islink', None)
            if native is not None:
                stack.enter_context(patch.object(nt, '_path_islink', lambda p: kind == 'symlink' if selected(p) else native(p)))
            native_junction = getattr(nt, '_path_isjunction', None)
            if native_junction is not None:
                stack.enter_context(patch.object(nt, '_path_isjunction', lambda p: kind == 'junction' if selected(p) else native_junction(p)))
        yield


def simulated_symlink(path, target=None):
    return simulated_link(path, target, kind='symlink')


@contextlib.contextmanager
def fixture_processes(respond):
    """One harmless launch boundary shared by run/check_output/direct Popen.

    Never starts a real child. `respond` supplies a CompletedProcess or raises
    an execution error; both the caller's argv and Popen kwargs are observed.
    """
    calls = []
    signature = POPEN_SIGNATURE

    class Process:
        def __init__(self, args, *positional, **kwargs):
            bound = signature.bind(args, *positional, **kwargs)
            kwargs = {key: value for key, value in bound.arguments.items() if key != 'args'}
            calls.append((args, kwargs))
            result = respond(args, kwargs)
            self.args, self.returncode = args, result.returncode
            self.text_mode = bool(kwargs.get('text') or kwargs.get('universal_newlines') or kwargs.get('encoding'))

            def convert(value):
                if self.text_mode:
                    return value.decode(kwargs.get('encoding') or 'utf-8') if isinstance(value, bytes) else (value or '')
                return value.encode(kwargs.get('encoding') or 'utf-8') if isinstance(value, str) else (value or b'')

            self._out, self._err = convert(result.stdout), convert(result.stderr)
            if kwargs.get('stderr') == subprocess.STDOUT:
                self._out += self._err
            stream = io.StringIO if self.text_mode else io.BytesIO
            self.stdout = stream(self._out) if kwargs.get('stdout') == subprocess.PIPE else None
            self.stderr = stream(self._err) if kwargs.get('stderr') == subprocess.PIPE else None
            self.stdin = stream() if kwargs.get('stdin') == subprocess.PIPE else None

        def communicate(self, input=None, timeout=None):
            return (self._out if self.stdout is not None else None,
                    self._err if self.stderr is not None else None)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

        terminate = kill

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch.object(subprocess, 'Popen', Process):
        yield calls


@contextlib.contextmanager
def observe_routes_hashing(route_bytes):
    """Allow routes JSON parsing, observe its byte SHA-256 computation instead.

    Other selected files are observed at open; routes bytes are permitted as
    validation input. Public hashlib sha256/new/update/copy paths are covered.
    This is instrumentation, not a claim about native/internal hashing APIs.
    """
    attempts = []
    original_sha256, original_new = hashlib.sha256, hashlib.new

    class Hash:
        def __init__(self, wrapped, initial=b''):
            self.wrapped, self.data = wrapped, bytearray()
            self.record(initial)

        def record(self, value):
            self.data.extend(value)
            if route_bytes and route_bytes in self.data:
                attempts.append('routes-sha256')

        def update(self, value):
            self.record(value)
            self.wrapped.update(value)

        def copy(self):
            return Hash(self.wrapped.copy(), bytes(self.data))

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    def sha256(*args, **kwargs):
        wrapped = original_sha256(*args, **kwargs)
        initial = args[0] if args else kwargs.get('string', kwargs.get('data', b''))
        return Hash(wrapped, initial)

    def new(name, data=b'', **kwargs):
        value = original_new(name, data, **kwargs)
        return Hash(value, data) if name.lower().replace('-', '') == 'sha256' else value

    with patch.object(hashlib, 'sha256', sha256), patch.object(hashlib, 'new', new):
        yield attempts


@contextlib.contextmanager
def unavailable_git():
    """Only Git lookup/execution is unavailable; repo, index and Python stay valid."""
    original_which = shutil.which
    attempts = []

    def is_git(command):
        return Path(os.fsdecode(command)).stem.lower() == 'git'

    def fail(args, kwargs):
        command = args[0] if isinstance(args, (list, tuple)) else args
        if is_git(command):
            attempts.append('execute')
            raise FileNotFoundError(2, 'fixture: Git execution unavailable', str(command))
        raise AssertionError(f'Unexpected non-Git child on unavailable path: {command}')

    def which(command, *a, **kw):
        if is_git(command):
            attempts.append('lookup')
            return None
        return original_which(command, *a, **kw)

    with fixture_processes(fail), patch.object(shutil, 'which', which):
        yield attempts


class ContextEntry(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='hglos-context-oracle-')
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.assertFalse(self.tmp.is_relative_to(REPO), 'TMP/TEMP must be outside repository')
        self.root = self.tmp / 'package'
        self.root.mkdir()
        self.routes = {'schema_version': 1, 'tasks': {'answer': []}}
        for task in TASKS[1:]:
            skill = f'skills/hglos-{task}/SKILL.md'
            reference = f'skills/hglos-{task}/references/needed.md'
            self.routes['tasks'][task] = [skill, reference]
            for rel in (skill, reference):
                self.write(rel, '# ' + rel + '\n')
        for name in COMMON:
            if name != 'context_routes.json':
                self.write(name, '# ' + name + '\n')
        self.write_routes()
        self.repo = self.tmp / 'project'
        self.repo.mkdir()
        self.git('init', '-b', 'oracle-fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'fixture')
        (self.repo / 'tracked.txt').write_text('initial\n', encoding='utf-8')
        self.git('add', 'tracked.txt')
        self.git('commit', '-m', 'fixture', '-q')
        self.head = self.git('rev-parse', 'HEAD').stdout.strip()

    def write(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')
        return p

    def write_routes(self):
        self.write('context_routes.json', json.dumps(self.routes))

    def git(self, *args, repo=None):
        cmd = ['git', '--no-pager', '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-c', 'core.untrackedCache=false', '-c', f'core.hooksPath={os.devnull}',
               '-C', str(repo or self.repo), *args]
        p = subprocess.run(cmd, env=safe_env(), capture_output=True, text=True,
                           encoding='utf-8', timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def invoke(self, command, *, task='verify', root=None, extra=(), env=None):
        return subprocess.run([sys.executable, '-B', str(CLI), command, '--layout', 'source',
                               '--root', str(root or self.root), '--task', task, *map(str, extra)],
                              cwd=self.tmp, env=env or safe_env(), capture_output=True,
                              text=True, encoding='utf-8', timeout=30)

    def result(self, p, rc):
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        self.assertEqual(p.stderr, '', p.stderr)
        obj = json.loads(p.stdout)
        self.assertIsInstance(obj, dict)
        if rc:
            self.assertEqual(set(obj), {'error'})
            self.assertEqual(set(obj['error']), {'code', 'message'})
            for key in ('code', 'message'):
                self.assertIsInstance(obj['error'][key], str)
                self.assertTrue(obj['error'][key])
        return obj

    def build(self, task='verify'):
        return self.result(self.invoke('build', task=task), 0)

    def index(self, task='verify'):
        # Independent construction permits negative tests before build exists.
        files = sorted(set(COMMON) | set(self.routes['tasks'][task]))
        return dict(schema_version=1, derived=True, root=str(self.root), layout='source',
                    task=task, files=[dict(path=p, sha256=digest(self.root / p)) for p in files])

    def check(self, index=None, *, task='verify', extra=(), env=None, rc=0):
        p = self.tmp / 'input-index.json'
        p.write_text(json.dumps(self.index(task) if index is None else index), encoding='utf-8')
        before = (digest(p), p.stat().st_mtime_ns)
        result = self.invoke('check', task=task,
                             extra=('--repo', self.repo, '--index', p, *extra), env=env)
        self.assertEqual((digest(p), p.stat().st_mtime_ns), before, 'check must never rebuild its input')
        return self.result(result, rc)

    def limits(self, receipt):
        self.assertIs(receipt['approval_verified'], False)
        self.assertEqual(receipt['model_rules_read'], 'not_attested')
        self.assertIs(receipt['effective_policy_verified'], False)
        self.assertEqual(receipt['project_instruction_coverage'], 'not_checked')

    def in_process(self, args):
        with patch.object(sys, 'argv', [str(CLI), *args]):
            try:
                runpy.run_path(str(CLI), run_name='__main__')
            except SystemExit as exc:
                return exc.code
        return 0

    def assert_sentinel_rejected_without_read(self, sentinel, operation, index, *, aliases=()):
        args = [operation, '--layout', 'source', '--root', str(self.root), '--task', 'verify']
        if operation == 'check':
            path = self.tmp / 'sentinel-index.json'
            path.write_text(json.dumps(index), encoding='utf-8')
            args += ['--repo', str(self.repo), '--index', str(path)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with observe_sentinel_reads(sentinel, *aliases) as attempts:
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = self.in_process(args)
            finally:
                self.assertEqual(attempts, [], 'external sentinel content was opened')
        return self.result(SimpleNamespace(returncode=rc, stdout=stdout.getvalue(), stderr=stderr.getvalue()), 2)

    def check_in_process(self, index):
        path = self.tmp / 'in-process-index.json'
        path.write_text(json.dumps(index), encoding='utf-8')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.in_process(['check', '--layout', 'source', '--root', str(self.root),
                                  '--task', 'verify', '--repo', str(self.repo), '--index', str(path)])
        return SimpleNamespace(returncode=rc, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    def assert_link_rejected_without_read(self, alias, target, route):
        # Fixture hash preparation happens before either content observer starts.
        target_hash = digest(target)
        for operation in ('build', 'check'):
            with self.subTest(operation=operation):
                with self.verify_attack_fixture(route, target_hash) as index:
                    self.assert_sentinel_rejected_without_read(target, operation, index, aliases=(alias,))

    def duplicate_key_index_json(self):
        index = self.index()
        raw = '{"schema_version":1,' + json.dumps(index)[1:]
        # A parser that silently keeps the last key sees a fully valid index.
        self.assertEqual(json.loads(raw), index)
        return raw

    def assert_normal_verify_control(self):
        self.assertEqual(self.build('verify'), self.index('verify'))
        self.assertIs(self.check(self.index('verify'))['content_match'], True)

    @contextlib.contextmanager
    def verify_attack_fixture(self, path, sha256):
        """Add one attack, retaining every normal verify skill/reference/file."""
        original = copy.deepcopy(self.routes)
        normal = self.index('verify')
        normal_paths = {item['path'] for item in normal['files']}
        self.routes['tasks']['verify'].append(path)
        self.write_routes()
        index = copy.deepcopy(normal)
        for item in index['files']:
            if item['path'] == 'context_routes.json':
                item['sha256'] = digest(self.root / item['path'])
        index['files'].append(dict(path=path, sha256=sha256))
        index['files'].sort(key=lambda item: item['path'])
        try:
            self.assertEqual(self.routes['tasks']['verify'], original['tasks']['verify'] + [path])
            self.assertEqual({item['path'] for item in index['files']}, normal_paths | {path})
            yield index
        finally:
            self.routes = original
            self.write_routes()

    def test_build_all_explicit_tasks_exact_selected_files_and_hashes(self):
        for task in TASKS:
            with self.subTest(task=task):
                obj = self.build(task)
                self.assertEqual(obj, self.index(task))
                self.assertNotIn('approved', obj)
                self.assertNotIn('complete', obj)

    def test_check_receipt_clean_repo_and_unpinned_limits(self):
        receipt = self.check()
        self.assertIs(receipt['content_match'], True)
        self.assertEqual(receipt['index_sha256'], digest(self.tmp / 'input-index.json'))
        self.assertEqual(receipt['files'], self.index()['files'])
        self.assertEqual(receipt['repo'], dict(root=str(self.repo.resolve()), branch='oracle-fixture',
                                              head=self.head, unborn=False, dirty=False))
        self.assertNotEqual(receipt['baseline'], 'pinned')
        self.limits(receipt)

    def test_unknown_task_and_invalid_layout_are_json_input_errors(self):
        self.result(self.invoke('build', task='guess-this'), 2)
        p = subprocess.run([sys.executable, '-B', str(CLI), 'build', '--layout', 'guess',
                            '--root', str(self.root), '--task', 'answer'], env=safe_env(),
                           capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.result(p, 2)

    def test_missing_common_rule_never_becomes_success(self):
        idx = self.index()
        (self.root / 'RULES.md').unlink()
        self.check(idx, rc=2)
        self.result(self.invoke('build', task='answer'), 2)

    def test_content_changes_are_mismatch_including_routes_entry_skill_reference(self):
        for rel in ('RULES.md', 'ENTRY.md', 'context_routes.json',
                    'skills/hglos-verify/SKILL.md', 'skills/hglos-verify/references/needed.md'):
            with self.subTest(path=rel):
                idx = self.index()
                p = self.root / rel
                old = p.read_bytes()
                p.write_bytes(old + b'\n')
                try:
                    self.check(idx, rc=1)
                finally:
                    p.write_bytes(old)

    def test_strict_index_schema_malformed_duplicate_unknown_missing_and_wrong_types(self):
        variants = []
        for key, value in [('schema_version', 2), ('schema_version', True), ('derived', False),
                           ('task', 'design'), ('root', str(self.tmp)), ('layout', 'installed'),
                           ('files', {}), ('unexpected', 'ignored?')]:
            idx = self.index()
            idx[key] = value
            variants.append((key + repr(value), idx))
        idx = self.index(); idx['files'].append(copy.deepcopy(idx['files'][0])); variants.append(('duplicate', idx))
        idx = self.index(); idx['files'].pop(); variants.append(('missing', idx))
        idx = self.index(); idx['files'][0]['sha256'] = 'invalid'; variants.append(('bad-hash', idx))
        self.write('unselected.md', 'not selected')
        idx = self.index(); idx['files'].append(dict(path='unselected.md', sha256=digest(self.root / 'unselected.md')))
        variants.append(('unselected', idx))
        for name, idx in variants:
            with self.subTest(case=name):
                self.check(idx, rc=2)

    def test_invalid_json_duplicate_json_keys_and_missing_index(self):
        p = self.tmp / 'invalid.json'
        for raw in ('{', '[]', 'null'):
            with self.subTest(raw=raw):
                p.write_text(raw, encoding='utf-8')
                self.result(self.invoke('check', extra=('--repo', self.repo, '--index', p)), 2)
        self.result(self.invoke('check', extra=('--repo', self.repo, '--index', self.tmp / 'absent')), 2)

    def test_duplicate_key_in_otherwise_valid_index_rejected(self):
        p = self.tmp / 'duplicate-valid-index.json'
        p.write_text(self.duplicate_key_index_json(), encoding='utf-8')
        self.result(self.invoke('check', extra=('--repo', self.repo, '--index', p)), 2)

    def test_existing_external_sentinel_rejected_before_content_read(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        sentinel = self.tmp / 'outside.md'
        sentinel.write_text('EXTERNAL_SENTINEL_DO_NOT_LOAD\n', encoding='utf-8')
        self.assertTrue(sentinel.is_file())
        self.assertFalse(sentinel.is_relative_to(self.root))
        sentinel_hash = digest(sentinel)  # Fixture preparation, before observation begins.
        for path in ('../outside.md', '..\\outside.md', str(sentinel), sentinel.as_posix()):
            for operation in ('build', 'check'):
                with self.subTest(path=path, operation=operation):
                    with self.verify_attack_fixture(path, sentinel_hash) as index:
                        self.assert_sentinel_rejected_without_read(sentinel, operation, index)

    def test_route_and_index_reject_absolute_parent_url_command_and_glob(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        bad = ('../outside.md', str(self.tmp / 'outside.md'), 'https://example.invalid/policy',
               'file:///outside.md', '$(echo unsafe)', '*.md', '..\\outside.md')
        for path in bad:
            for operation in ('build', 'check'):
                with self.subTest(path=path, operation=operation):
                    with self.verify_attack_fixture(path, '0' * 64) as index:
                        if operation == 'check':
                            self.check(index, rc=2)
                        else:
                            self.result(self.invoke('build'), 2)

    def test_routes_reject_unknown_schema_fields_and_command_fields(self):
        for key, value in [('command', ['echo', 'unsafe']), ('schema_version', 9), ('tasks', [])]:
            with self.subTest(key=key):
                original = copy.deepcopy(self.routes)
                self.routes[key] = value
                self.write_routes()
                try:
                    self.result(self.invoke('build'), 2)
                finally:
                    self.routes = original
                    self.write_routes()

    def test_rebuilt_rules_plus_index_match_is_not_approval_and_pin_detects_change(self):
        original = self.index()
        self.check(original)
        old_pin = digest(self.tmp / 'input-index.json')
        self.write('RULES.md', 'changed rules\n')
        receipt = self.check(self.index())
        self.limits(receipt)
        self.assertNotEqual(receipt['baseline'], 'pinned')
        self.check(self.index(), extra=('--expect-index-sha256', old_pin), rc=1)
        pin = digest(self.tmp / 'input-index.json')
        receipt = self.check(self.index(), extra=('--expect-index-sha256', pin))
        self.assertEqual(receipt['baseline'], 'pinned')
        self.limits(receipt)
        self.check(extra=('--expect-index-sha256', 'invalid'), rc=2)

    def test_dirty_detached_unborn_and_privacy(self):
        secret = 'private-path-TOKEN-should-not-appear'
        (self.repo / secret).write_text('SECRET_CONTENT', encoding='utf-8')
        receipt = self.check()
        self.assertIs(receipt['repo']['dirty'], True)
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertNotIn('SECRET_CONTENT', json.dumps(receipt))
        self.git('checkout', '--detach', '-q')  # Temporary fixture only.
        self.assertIsNone(self.check()['repo']['branch'])
        unborn = self.tmp / 'unborn'
        unborn.mkdir()
        self.git('init', '-b', 'new-branch', repo=unborn)
        self.repo = unborn
        receipt = self.check()
        self.assertEqual(receipt['repo'], dict(root=str(unborn), branch='new-branch', head=None,
                                              unborn=True, dirty=False))
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertNotIn('SECRET_CONTENT', json.dumps(receipt))

    def test_nonrepo_is_query_error(self):
        self.repo = self.tmp / 'not-git'
        self.repo.mkdir()
        self.check(rc=2)

    def test_git_unavailable_on_valid_repo_is_query_error(self):
        with self.subTest(control='normal-git-valid-repo-index'):
            self.assert_normal_verify_control()
        with self.subTest(case='only-git-unavailable'):
            self.assertTrue((self.repo / '.git').is_dir())
            index = self.index('verify')
            with unavailable_git() as attempts:
                response = self.check_in_process(index)
            self.result(response, 2)
            self.assertTrue(attempts, 'must observe Git lookup/execution failure on a valid repo')

    def marker_helper(self):
        marker = self.tmp / 'helper.marker'
        helper = self.tmp / 'helper.py'
        helper.write_text('from pathlib import Path\nimport sys\n'
                          f'Path({str(marker)!r}).write_text("EXECUTED")\n'
                          'sys.stdout.buffer.write(sys.stdin.buffer.read())\n', encoding='utf-8')
        return marker, f'"{sys.executable}" -B "{helper.as_posix()}"'

    def test_fsmonitor_pager_external_diff_helpers_and_index_unchanged(self):
        marker, command = self.marker_helper()
        for key in ('core.fsmonitor', 'core.pager', 'diff.external', 'diff.oracle.textconv'):
            self.git('config', key, command)
        (self.repo / 'tracked.txt').write_text('changed\n', encoding='utf-8')
        index = self.repo / '.git/index'
        before = (digest(index), index.stat().st_mtime_ns)
        receipt = self.check()
        self.assertIs(receipt['repo']['dirty'], True)
        self.assertFalse(marker.exists())
        self.assertEqual((digest(index), index.stat().st_mtime_ns), before)

    def test_clean_process_empty_included_and_worktree_filters_block_without_helpers(self):
        for kind, place, empty in [('clean', 'local', False), ('process', 'local', False),
                                   ('clean', 'local', True), ('process', 'include', False),
                                   ('clean', 'worktree', False)]:
            with self.subTest(kind=kind, place=place, empty=empty):
                marker, command = self.marker_helper()
                config = self.repo / '.git/config'
                old = config.read_bytes()
                included = self.tmp / 'included-config'
                if place == 'include':
                    included.write_text(f'[filter "oracle"]\n {kind} = {json.dumps(command)}\n', encoding='utf-8')
                    self.git('config', 'include.path', str(included))
                elif place == 'worktree':
                    self.git('config', 'extensions.worktreeConfig', 'true')
                    (self.repo / '.git/config.worktree').write_text(
                        f'[filter "oracle"]\n {kind} = {json.dumps(command)}\n', encoding='utf-8')
                else:
                    self.git('config', f'filter.oracle.{kind}', '' if empty else command)
                (self.repo / '.gitattributes').write_text('tracked.txt filter=oracle\n', encoding='utf-8')
                (self.repo / 'tracked.txt').write_text('modified for filter\n', encoding='utf-8')
                index = self.repo / '.git/index'
                before = (digest(index), index.stat().st_mtime_ns)
                try:
                    self.check(rc=2)
                    self.assertFalse(marker.exists())
                    self.assertEqual((digest(index), index.stat().st_mtime_ns), before)
                finally:
                    config.write_bytes(old)
                    (self.repo / '.git/config.worktree').unlink(missing_ok=True)

    def test_git_environment_injection_cannot_redirect_repo_or_run_helper(self):
        marker, command = self.marker_helper()
        other = self.tmp / 'other'
        other.mkdir()
        self.git('init', '-b', 'wrong-repo', repo=other)
        env = safe_env()
        env.update(GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other),
                   GIT_INDEX_FILE=str(self.tmp / 'injected-index'),
                   GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.fsmonitor', GIT_CONFIG_VALUE_0=command,
                   GIT_CONFIG_PARAMETERS="'core.fsmonitor=" + command + "'", GIT_TRACE=str(self.tmp / 'trace'))
        receipt = self.check(env=env)
        self.assertEqual(receipt['repo']['root'], str(self.repo))
        self.assertEqual(receipt['repo']['head'], self.head)
        self.assertFalse(marker.exists())
        self.assertFalse((self.tmp / 'injected-index').exists())
        self.assertFalse((self.tmp / 'trace').exists())

    def test_junction_escape_rejected_without_reading_external_target(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        outside = self.tmp / 'outside'
        outside.mkdir()
        (outside / 'policy.md').write_text('external', encoding='utf-8')
        link = self.root / 'linked'
        # mklink is a cmd builtin, fixture path is explicit; cleanup removes only the junction.
        p = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(outside)],
                           capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, repr(p.stderr))
        self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        self.assert_link_rejected_without_read(link / 'policy.md', outside / 'policy.md', 'linked/policy.md')

    def test_junction_escape_rejected_via_api_fixture(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        target_folder = self.tmp / 'junction-target'
        target_folder.mkdir()
        outside = target_folder / 'policy.md'
        outside.write_text('EXTERNAL_JUNCTION_SENTINEL\n', encoding='utf-8')
        folder = self.root / 'junction-alias'
        folder.mkdir()
        alias = self.write('junction-alias/policy.md', 'EXTERNAL_JUNCTION_SENTINEL\n')
        # This is an API/shape fixture, never a real junction security claim.
        with simulated_link(folder, target_folder, kind='junction'):
            self.assertTrue(folder.is_junction())
            self.assert_link_rejected_without_read(alias, outside, 'junction-alias/policy.md')

    def test_symlink_escape_rejected_via_os_metadata_fixture(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        outside = self.tmp / 'symlink-target.md'
        outside.write_text('EXTERNAL_SYMLINK_SENTINEL\n', encoding='utf-8')
        alias = self.write('symlink-alias.md', 'EXTERNAL_SYMLINK_SENTINEL\n')
        self.assertFalse(alias.is_symlink())
        with simulated_symlink(alias, outside):
            self.assertTrue(alias.is_symlink())
            self.assertTrue(os.path.islink(alias))
            self.assertTrue(stat.S_ISLNK(os.lstat(alias).st_mode))
            self.assertFalse((self.root / 'HOST.md').is_symlink())
            self.assert_link_rejected_without_read(alias, outside, 'symlink-alias.md')

    def test_actual_symlink_escape_rejected_without_reading_external_target(self):
        with self.subTest(control='normal-verify-without-attack'):
            self.assert_normal_verify_control()
        outside = self.tmp / 'actual-symlink-target.md'
        outside.write_text('EXTERNAL_ACTUAL_SYMLINK_SENTINEL\n', encoding='utf-8')
        alias = self.root / 'actual-symlink.md'
        alias.symlink_to(outside)  # Permission failure is recorded, never skipped.
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        self.assert_link_rejected_without_read(alias, outside, 'actual-symlink.md')

    def test_git_builtin_allowlist_fixed_options_env_and_filter_before_status(self):
        # In-process tracing verifies call ordering and arguments, not just absence of marker.
        idx = self.tmp / 'trace-index.json'
        idx.write_text(json.dumps(self.index()), encoding='utf-8')
        for filter_state in ('none', 'present', 'query-error'):
            with self.subTest(filter_state=filter_state):
                with fixture_processes(lambda args, kw: self.fixture_git_reply(args, kw, filter_state)) as calls, \
                     contextlib.redirect_stdout(io.StringIO()):
                    rc = self.in_process(['check', '--layout', 'source', '--root', str(self.root),
                                          '--task', 'verify', '--repo', str(self.repo), '--index', str(idx)])
                self.assertEqual(rc, 0 if filter_state == 'none' else 2)
                commands = []
                for args, kwargs in calls:
                    self.assertIsInstance(args, (list, tuple))
                    self.assertFalse(kwargs.get('shell', False))
                    self.assertEqual(Path(args[0]).stem.lower(), 'git')
                    for arg in ('--no-pager', '--no-optional-locks', 'core.fsmonitor=false',
                                'core.untrackedCache=false', f'core.hooksPath={os.devnull}'):
                        self.assertIn(arg, args)
                    pos = args.index('-C')
                    self.assertEqual(Path(args[pos + 1]).resolve(), self.repo)
                    builtin = args[pos + 2]
                    self.assertIn(builtin, ('config', 'rev-parse', 'symbolic-ref', 'status'))
                    commands.append(builtin)
                    env = kwargs['env']
                    self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
                    self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
                    self.assertEqual(env['GIT_CONFIG_SYSTEM'], os.devnull)
                    self.assertEqual({k for k in env if k.upper().startswith('GIT_')},
                                     {'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM'})
                    if builtin == 'config':
                        for arg in ('--includes', '--null', '--name-only', '--get-regexp', r'^filter\..*\.(clean|process)$'):
                            self.assertIn(arg, args)
                self.assertIn('config', commands)
                if filter_state == 'none':
                    self.assertLess(commands.index('config'), commands.index('status'))
                else:
                    self.assertNotIn('status', commands)

    def fixture_git_reply(self, args, kwargs, filter_state):
        """Harmless metadata for this fixed clean fixture; never spawn Git/helper."""
        self.assertIsInstance(args, (list, tuple))
        self.assertEqual(Path(args[0]).stem.lower(), 'git')
        pos = args.index('-C')
        builtin, options = args[pos + 2], args[pos + 3:]
        code, out = 0, ''
        if builtin == 'config':
            if filter_state == 'none':
                code = 1
            elif filter_state == 'present':
                out = 'filter.oracle.clean\0'
            else:
                code = 128
        elif builtin == 'status':
            pass
        elif builtin == 'symbolic-ref':
            out = ('oracle-fixture' if '--short' in options else 'refs/heads/oracle-fixture') + '\n'
        elif builtin == 'rev-parse':
            if '--show-toplevel' in options:
                out = str(self.repo) + '\n'
            elif '--git-dir' in options or '--absolute-git-dir' in options:
                out = str(self.repo / '.git') + '\n'
            elif '--is-inside-work-tree' in options:
                out = 'true\n'
            elif '--is-bare-repository' in options:
                out = 'false\n'
            elif (len([o for o in options if o in ('HEAD', 'HEAD^{commit}')]) == 1
                  and all(o in ('HEAD', 'HEAD^{commit}', '--verify', '--quiet', '-q', '--end-of-options') for o in options)):
                out = self.head + '\n'
            elif '--show-prefix' not in options:
                raise AssertionError(f'Unmodelled fixture query: {args}')
        else:
            raise AssertionError(f'Forbidden fixture child: {args}')
        return subprocess.CompletedProcess(args, code, out, '')

    def assert_filter_blocks_before_content(self, index, filter_state):
        """A filter veto must win even when a valid index is stale.

        Index/routes parsing is permitted. For other selected files, opening
        content is the comparison boundary; for routes, SHA-256 computation
        of its bytes is the boundary, not the read needed for schema validation.
        """
        path = self.tmp / 'filter-order-index.json'
        path.write_text(json.dumps(index), encoding='utf-8')
        selected = [self.root / item['path'] for item in index['files'] if item['path'] != 'context_routes.json']
        routes_bytes = (self.root / 'context_routes.json').read_bytes()

        stdout, stderr = io.StringIO(), io.StringIO()
        with observe_sentinel_reads(selected[0], *selected[1:]) as reads, observe_routes_hashing(routes_bytes) as hashes:
            try:
                with fixture_processes(lambda args, kw: self.fixture_git_reply(args, kw, filter_state)) as calls, \
                     contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = self.in_process(['check', '--layout', 'source', '--root', str(self.root),
                        '--task', 'verify', '--repo', str(self.repo), '--index', str(path)])
            finally:
                self.assertEqual(reads, [], 'selected content was opened on a filter-blocked path')
                self.assertEqual(hashes, [], 'routes content was hashed on a filter-blocked path')
        self.result(SimpleNamespace(returncode=rc, stdout=stdout.getvalue(), stderr=stderr.getvalue()), 2)
        self.assertTrue(any('config' in args and '--get-regexp' in args for args, kw in calls),
                        'must reach filter preflight, not reject a different precondition')
        self.assertFalse(any('status' in args for args, kw in calls), 'filter veto must precede status')

    def test_filter_blocks_stale_index_before_selected_content_read(self):
        index = self.index('verify')
        rule = self.root / 'RULES.md'
        original = rule.read_bytes()
        with self.subTest(control='matching-index-no-filter'):
            self.assertIs(self.check(index)['content_match'], True)
        # Each blocked case starts from the same complete, structurally valid
        # index. Only current content changes, so schema/selection stays valid.
        for filter_state in ('present', 'query-error'):
            with self.subTest(filter_state=filter_state, index='stale'):
                rule.write_bytes(original + b'changed-after-index\n')
                try:
                    self.assert_filter_blocks_before_content(index, filter_state)
                finally:
                    rule.write_bytes(original)
        with self.subTest(control='stale-valid-index-no-filter'):
            rule.write_bytes(original + b'changed-after-index\n')
            try:
                self.check(index, rc=1)
            finally:
                rule.write_bytes(original)

    def files_snapshot(self, root):
        return {p.relative_to(root).as_posix(): (digest(p), p.stat().st_mtime_ns)
                for p in root.rglob('*') if p.is_file()}

    def assert_cli_call_writes_nothing(self, action):
        before_tmp = self.files_snapshot(self.tmp)
        before_package, before_repo = self.files_snapshot(self.root), self.files_snapshot(self.repo)
        try:
            return action()
        finally:
            self.assertEqual(self.files_snapshot(self.tmp), before_tmp,
                             'CLI changed cwd/temp file names, content or mtime')
            self.assertEqual(self.files_snapshot(self.root), before_package)
            self.assertEqual(self.files_snapshot(self.repo), before_repo)

    def test_cli_writes_no_package_repo_or_index_files(self):
        # Harness input preparation is outside both CLI observation windows.
        path = self.tmp / 'no-write-input-index.json'
        path.write_text(json.dumps(self.index('verify')), encoding='utf-8')
        for operation in ('build', 'check'):
            with self.subTest(operation=operation):
                extra = ('--repo', self.repo, '--index', path) if operation == 'check' else ()
                response = self.assert_cli_call_writes_nothing(lambda: self.invoke(operation, extra=extra))
                self.result(response, 0)


if __name__ == '__main__':
    unittest.main()
