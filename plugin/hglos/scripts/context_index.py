#!/usr/bin/env python3
"""Read-only HGLOS content index; a match never attests approval or model reading."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys

TASKS = ('answer', 'design', 'implement', 'verify', 'dashboard', 'coordinate')
COMMON = ('ENTRY.md', 'context_routes.json', 'RULES.md', 'HOST.md', 'COORDINATION.md')
HEX = re.compile(r'[0-9a-f]{64}\Z')
INDEX_KEYS = {'schema_version', 'derived', 'root', 'layout', 'task', 'files'}
GITLINK = 'Gitlink/submodule and sparse index entries are unsupported'


class Failure(Exception):
    def __init__(self, message, code='invalid_input', status=2):
        super().__init__(message)
        self.code, self.status = code, status


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Failure('Duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(token):
    raise Failure('Invalid JSON constant')


def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Failure('Invalid UTF-8 JSON') from exc


def no_links(path):
    """Check lexical ancestors before resolving, without reading link targets."""
    path = Path(os.path.abspath(path))
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            raise Failure('Required path is missing', 'missing_path') from None
        if (current.is_symlink() or getattr(current, 'is_junction', lambda: False)() or
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise Failure('Symlink/junction paths are unsupported', 'unsafe_path')
    return path


def relative_path(value):
    if (not isinstance(value, str) or not value or not re.fullmatch(r'[A-Za-z0-9_. /-]+', value)
            or '\\' in value or value.startswith('/') or value.endswith('/')):
        raise Failure('Only explicit relative package paths are allowed', 'unsafe_path')
    parts = value.split('/')
    if any(p in ('', '.', '..') or p != p.strip() or p.endswith('.') for p in parts):
        raise Failure('Noncanonical relative path', 'unsafe_path')
    if any(re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) for p in parts):
        raise Failure('Reserved path component', 'unsafe_path')
    return value


def package_file(root, name):
    path = no_links(root / relative_path(name))
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise Failure('Package file is unavailable or outside its root', 'unsafe_path')
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def routes(root):
    value = parse_json(package_file(root, 'context_routes.json').read_bytes())
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'tasks'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            not isinstance(value['tasks'], dict) or set(value['tasks']) != set(TASKS)):
        raise Failure('Invalid routes schema')
    for names in value['tasks'].values():
        if not isinstance(names, list):
            raise Failure('Each route must be a list of explicit paths')
        names = [relative_path(name) for name in names]
        if len({p.casefold() for p in names}) != len(names):
            raise Failure('Duplicate route path')
    return value['tasks']


def installed_task(root):
    # Never inspect sibling/global skills. On renamed roots, inspect only this
    # skill's declared frontmatter (after the filter veto during check).
    if root.name in {'hglos-' + task for task in TASKS[1:]}:
        return root.name.removeprefix('hglos-')
    text = package_file(root, 'SKILL.md').read_text(encoding='utf-8')
    match = re.search(r'^name:\s*hglos-(design|implement|verify|dashboard|coordinate)\s*$', text, re.M)
    if not match:
        raise Failure('Cannot identify this installed skill')
    return match[1]


def selected_paths(root, layout, task):
    mapping = routes(root)
    if layout == 'installed' and task not in ('answer', installed_task(root)):
        raise Failure('Specify the independent installed skill for this task', 'wrong_installed_task')
    names = list(mapping[task])
    if task != 'answer':
        skill = f'skills/hglos-{task}/SKILL.md'
        if skill not in names:
            raise Failure('Route omits its selected skill')
        if layout == 'installed':
            prefix = f'skills/hglos-{task}/'
            names = [name[len(prefix):] if name.startswith(prefix) else name for name in names]
    chosen = sorted(set(COMMON) | set(names))
    if len({p.casefold() for p in chosen}) != len(chosen):
        raise Failure('Aliased selected paths')
    for name in chosen:
        package_file(root, name)
    return chosen


def validate_index(value, root, layout, task):
    if not isinstance(value, dict) or set(value) != INDEX_KEYS:
        raise Failure('Invalid index fields')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['derived'] is not True or value['root'] != str(root) or
            value['layout'] != layout or value['task'] != task or not isinstance(value['files'], list)):
        raise Failure('Index schema/root/layout/task does not match')
    names = []
    for item in value['files']:
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256'} or
                not isinstance(item['sha256'], str) or not HEX.fullmatch(item['sha256'])):
            raise Failure('Invalid file entry')
        names.append(relative_path(item['path']))
    if len({p.casefold() for p in names}) != len(names):
        raise Failure('Duplicate/aliased index paths')
    for name in names:
        package_file(root, name)
    return names


def git_environment():
    child = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    child.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return child


def git(repo, builtin, *args):
    if builtin not in ('config', 'rev-parse', 'symbolic-ref', 'status'):
        raise Failure('Unsupported Git operation', 'git_query')
    command = ['git', '--no-pager', '--no-optional-locks', '--no-lazy-fetch', '-c', 'core.fsmonitor=false',
               '-c', 'core.untrackedCache=false', '-c', f'core.hooksPath={os.devnull}',
               '-C', str(repo), builtin, *args]
    try:
        return subprocess.run(command, shell=False, env=git_environment(), capture_output=True,
                              text=True, encoding='utf-8', errors='strict', timeout=15)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise Failure('Git metadata query unavailable', 'git_query') from exc


def git_preflight(repo):
    query = git(repo, 'config', '--includes', '--null', '--name-only', '--get-regexp', r'^filter\..*\.(clean|process)$')
    if query.returncode != 1 or query.stdout:
        raise Failure('Git clean/process filter present or configuration query failed', 'git_filter')


def validate_git_index(raw):
    """Validate a normal index without asking Git to refresh/load its contents.

    Reject gitlinks before status can inspect nested repositories. Split index
    loading can freshen sharedindex metadata even with optional locks disabled;
    sparse/split/unknown mandatory formats are therefore unsupported here.
    """
    def invalid():
        raise Failure('Unsupported or invalid Git index', 'git_index')

    if len(raw) < 32 or raw[:4] != b'DIRC':
        invalid()
    if hashlib.sha1(raw[:-20]).digest() == raw[-20:]:
        sizes = (20,)
    elif len(raw) >= 44 and hashlib.sha256(raw[:-32]).digest() == raw[-32:]:
        sizes = (32,)
    elif not any(raw[-20:]):
        # index.skipHash writes a null trailer that Git accepts. Its hash size is
        # then unknown: exactly one size must parse, and any gitlink rejects.
        sizes = (20, 32) if len(raw) >= 44 and not any(raw[-32:]) else (20,)
    else:
        invalid()

    def entries(oid_size):
        version, count = struct.unpack_from('!II', raw, 4)
        if version not in (2, 3, 4):
            invalid()
        end, offset, previous_name = len(raw) - oid_size, 12, b''
        for _ in range(count):
            start = offset
            fixed = 40 + oid_size + 2
            if offset + fixed > end:
                invalid()
            mode = struct.unpack_from('!I', raw, offset + 24)[0]
            if stat.S_IFMT(mode) in (0o160000, stat.S_IFDIR):
                raise Failure(GITLINK, 'git_index')
            flags = struct.unpack_from('!H', raw, offset + 40 + oid_size)[0]
            offset += fixed
            if flags & 0x4000:
                if version == 2 or offset + 2 > end:
                    invalid()
                if struct.unpack_from('!H', raw, offset)[0] & ~0x6000:
                    invalid()
                offset += 2
            remove = 0
            if version == 4:
                # Strip-count varint, followed by the NUL-terminated path suffix.
                for _ in range(10):
                    if offset >= end:
                        invalid()
                    byte = raw[offset]
                    offset += 1
                    remove = (remove << 7) + (byte & 0x7f)
                    if not byte & 0x80:
                        break
                    remove += 1
                else:
                    invalid()
            nul = raw.find(b'\0', offset, end)
            if nul < 0:
                invalid()
            suffix = raw[offset:nul]
            if remove > len(previous_name):
                invalid()
            name = previous_name[:len(previous_name)-remove] + suffix if version == 4 else suffix
            if not name or (flags & 0xfff != 0xfff and flags & 0xfff != len(name)):
                invalid()
            previous_name = name
            offset = nul + 1
            if version < 4:
                offset = start + ((offset - start + 7) // 8) * 8
                if raw[nul:offset] != b'\0' * (offset - nul):
                    invalid()
            if offset > end:
                invalid()
        while offset < end:
            if offset + 8 > end:
                invalid()
            name, size = raw[offset:offset+4], struct.unpack_from('!I', raw, offset+4)[0]
            if name in (b'link', b'sdir', b'EOIE', b'IEOT') or not 65 <= name[0] <= 90:
                invalid()
            offset += 8 + size
            if offset > end:
                invalid()

    parsed = []
    for oid_size in sizes:
        try:
            entries(oid_size)
            parsed.append(oid_size)
        except Failure as exc:
            if len(sizes) == 1 or str(exc) == GITLINK:
                raise
    if len(parsed) != 1:
        invalid()

def index_preflight(repo):
    query = git(repo, 'rev-parse', '--git-dir')
    if query.returncode or not query.stdout.strip():
        raise Failure('Git metadata directory unavailable', 'git_query')
    directory = Path(query.stdout.strip())
    if not directory.is_absolute():
        directory = repo / directory  # relative to -C, not Python's cwd
    directory = no_links(directory).resolve()
    path = directory / 'index'
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    validate_git_index(no_links(path).read_bytes())
    return True


def repo_metadata(repo):
    top = git(repo, 'rev-parse', '--show-toplevel')
    if top.returncode:
        raise Failure('Git repository root unavailable', 'git_query')
    physical = no_links(top.stdout.strip()).resolve()
    branch = git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if branch.returncode not in (0, 1):
        raise Failure('Git branch unavailable', 'git_query')
    name = branch.stdout.strip() if branch.returncode == 0 else None
    if branch.returncode == 0 and not name:
        raise Failure('Empty Git branch response', 'git_query')
    head = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')
    unborn = head.returncode != 0
    if unborn and (name is None or head.returncode not in (1, 128)):
        raise Failure('Git revision unavailable', 'git_query')
    if unborn:
        bare_head = git(repo, 'rev-parse', '--verify', 'HEAD')
        if bare_head.returncode not in (1, 128):
            raise Failure('Existing HEAD cannot be read as a commit', 'git_query')
    revision = head.stdout.strip() if not unborn else None
    if revision is not None and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise Failure('Invalid Git revision response', 'git_query')
    if not index_preflight(repo) and not unborn:
        raise Failure('Missing index on an existing revision is unsupported', 'git_index')
    # 'dirty' suppresses nested worktree scans without hiding staged gitlink
    # deletion from HEAD. Current index gitlinks were rejected above.
    status = git(repo, 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=dirty')
    if status.returncode:
        raise Failure('Git status unavailable', 'git_query')
    return dict(root=str(physical), branch=name, head=revision, unborn=unborn, dirty=bool(status.stdout))


def execute(args):
    root = no_links(args.root).resolve()
    if not root.is_dir():
        raise Failure('Package root must be a directory')
    if args.command == 'build':
        chosen = selected_paths(root, args.layout, args.task)
        return dict(schema_version=1, derived=True, root=str(root), layout=args.layout, task=args.task,
                    files=[dict(path=name, sha256=digest(package_file(root, name))) for name in chosen])
    if not args.index or not args.repo:
        raise Failure('check requires --repo and --index')
    raw = no_links(args.index).read_bytes()
    value = parse_json(raw)
    indexed = validate_index(value, root, args.layout, args.task)
    if args.expect_index_sha256 is not None and not HEX.fullmatch(args.expect_index_sha256):
        raise Failure('Expected index pin must be a lowercase SHA-256')
    repo = no_links(args.repo).resolve()
    git_preflight(repo)  # Before status or content/pin comparison (including rc1).
    chosen = selected_paths(root, args.layout, args.task)
    if set(indexed) != set(chosen):
        raise Failure('Index selection does not match routes')
    index_hash = hashlib.sha256(raw).hexdigest()
    if args.expect_index_sha256 and index_hash != args.expect_index_sha256:
        raise Failure('External index pin differs', 'pin_mismatch', 1)
    metadata = repo_metadata(repo)
    current = [dict(path=name, sha256=digest(package_file(root, name))) for name in chosen]
    expected = {item['path']: item['sha256'] for item in value['files']}
    if any(item['sha256'] != expected[item['path']] for item in current):
        raise Failure('Selected package content differs', 'content_mismatch', 1)
    return dict(schema_version=1, derived=True, root=str(root), layout=args.layout, task=args.task,
                content_match=True, files=current, repo=metadata, index_sha256=index_hash,
                baseline='pinned' if args.expect_index_sha256 else 'unpinned', approval_verified=False,
                model_rules_read='not_attested', effective_policy_verified=False,
                project_instruction_coverage='not_checked')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = Parser(description=__doc__)
    parser.add_argument('command', choices=('build', 'check'))
    parser.add_argument('--layout', required=True, choices=('source', 'installed'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--task', required=True, choices=TASKS)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--expect-index-sha256')
    try:
        result, code = execute(parser.parse_args(argv)), 0
    except Failure as exc:
        result, code = dict(error=dict(code=exc.code, message=str(exc))), exc.status
    except (OSError, ValueError, UnicodeError):
        result, code = dict(error=dict(code='input_io', message='Input or package file unavailable')), 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
