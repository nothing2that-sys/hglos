#!/usr/bin/env python3
"""Preview or explicitly install project-local coordination hooks and instructions."""

import argparse
import base64
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

from coordination import CoordinationError, MARKER, initialize, no_links

START = '<!-- HGLOS-COORDINATION v1 -->'
END = '<!-- /HGLOS-COORDINATION -->'
BLOCK = (START + '\nRead `.hglos/COORDINATION.md` at session start and follow its cooperative '
         'ownership protocol before writes. Other participants may be active without being mentioned. '
         'Coordination does not grant additional task or tool permissions.\n' + END)
# Hooks run a pinned copy so reinstalling or upgrading the skill cannot break live hooks.
BIN = Path('.hglos/bin')
PINNED = ('coordination.py', 'coordination_hook.py')
MANIFEST = 'manifest.json'
MISSING = 'HGLOS hook copy missing; rerun setup_coordination.py --apply'
# Managed marker: a trailing shell comment (a no-op in bash and PowerShell) rather than an extra JSON key,
# because host settings schemas may reject unknown handler keys.
MANAGED_MARK = 'hglos-coordination-v1'
MARK_SUFFIX = ' # ' + MANAGED_MARK
# Policies written by setup before manifest.json existed (release d844186): unmodified if bytes match.
LEGACY_POLICY_SHA256 = {
    'COORDINATION.md': {'4d291f39437adc62af6c81a800cda599a31aa33f080b91bcda58d77b028794be'},
    'RULES.md': {'4150e55123c8f6afa8b6924f43a59813fdb98f80578b10bbd621ac52414fb056'},
}
DIFF_LINES = 200


def command(parts, windows=False):
    if windows:
        return '& ' + ' '.join("'" + str(p).replace("'", "''") + "'" for p in parts)
    return shlex.join([str(p) for p in parts])


def guarded(parts, script, blocking, windows=False):
    """Run the pinned hook; if it or the coordinator it imports is missing, block PreToolUse (exit 2) only."""
    run = command(parts, windows)
    needed = (script, script.with_name('coordination.py'))
    if windows:
        # .NET check: Test-Path loads a module whose progress record pollutes stderr as CLIXML.
        exists = ' -and '.join("[IO.File]::Exists('" + str(p).replace("'", "''") + "')" for p in needed)
        missing = f"[Console]::Error.WriteLine('{MISSING}'); exit 2" if blocking else 'exit 0'
        return f'if ({exists}) {{ {run}; exit $LASTEXITCODE }} else {{ {missing} }}{MARK_SUFFIX}'
    exists = ' && '.join(f'[ -f {shlex.quote(str(p))} ]' for p in needed)
    missing = f"echo '{MISSING}' >&2; exit 2" if blocking else 'exit 0'
    return f'if {exists}; then {run}; else {missing}; fi{MARK_SUFFIX}'


def hooks_for(root, host, python):
    if host not in ('claude', 'codex'):
        raise CoordinationError('No automatic hook adapter for this host; use manual coordination')
    script = Path(root) / BIN / 'coordination_hook.py'
    events = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd']
    events += ['PostToolUseFailure', 'SubagentStop'] if host == 'claude' else ['Interrupt']
    hooks = {}
    for event in events:
        parts = [python, script, '--repo', root, '--host', host, '--event', event]
        blocking = event == 'PreToolUse'
        handler = {'type': 'command', 'command': guarded(parts, script, blocking), 'timeout': 30}
        if host == 'codex':
            # Explicit shell works whether the host launches via cmd or PowerShell.
            encoded = base64.b64encode(guarded(parts, script, blocking, True).encode('utf-16-le')).decode('ascii')
            handler['commandWindows'] = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
            if event in ('SessionEnd', 'Interrupt'):
                handler['timeout'] = 3
        elif os.name == 'nt':
            handler.update(command=guarded(parts, script, blocking, True), shell='powershell')
        if host == 'claude' and event == 'PreToolUse':
            handler['onFailure'] = 'block'
        hooks[event] = [{'hooks': [handler]}]
    return hooks


GUARDED_POSIX = re.compile(r"^if \[ -f .+? \](?: && \[ -f .+? \])?; then (?P<run>.+); "
                           r"else (?:exit 0|echo '[^']*' >&2; exit 2); fi$", re.S)
GUARDED_WINDOWS = re.compile(r"^if \(\[IO\.File\]::Exists\(.+?\)(?: -and \[IO\.File\]::Exists\(.+?\))?\) "
                             r"\{ (?P<run>.+); exit \$LASTEXITCODE \} "
                             r"else \{ (?:exit 0|\[Console\]::Error\.WriteLine\('[^']*'\); exit 2) \}$", re.S)
PS_TOKEN = re.compile(r"'((?:[^']|'')*)'")
HOOK_EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure',
               'Stop', 'SubagentStop', 'Interrupt', 'SessionEnd'}


def hook_argv(command):
    """argv of a hook command in any form setup has generated (guarded or plain, POSIX or PowerShell)."""
    for guard in (GUARDED_POSIX, GUARDED_WINDOWS):
        match = guard.match(command)
        if match:
            command = match.group('run')
            break
    if not command.startswith('& '):
        try:
            return shlex.split(command)
        except ValueError:
            return None
    tokens, rest, cursor = [], command[2:], 0
    while cursor < len(rest):
        match = PS_TOKEN.match(rest, cursor)
        if not match:
            return None
        tokens.append(match.group(1).replace("''", "'"))
        cursor = match.end()
        if cursor < len(rest):
            if rest[cursor] != ' ':
                return None
            cursor += 1
    return tokens


def ours(handler, root=None):
    """A managed HGLOS handler: `<interpreter> <HGLOS coordination_hook.py> --repo <THIS project> --host H --event E`.

    Marked handlers (this setup's guarded command + MANAGED_MARK) may use any interpreter, e.g. PyPy.
    Unmarked handlers predate the marker; old setup always wrote a python executable, so they count only
    when the program is python-named - an `echo`/`cat` that merely names an HGLOS script stays a user hook.
    The command is parsed, never substring-matched; another project's path is never ours.
    """
    command = handler.get('command') if isinstance(handler, dict) else None
    if not isinstance(command, str):
        return False
    marked = command.endswith(MARK_SUFFIX)
    if marked:
        body = command[:-len(MARK_SUFFIX)]
        if not (GUARDED_POSIX.match(body) or GUARDED_WINDOWS.match(body)):
            return False  # The marker is only valid on the exact guarded form setup generates.
        command = body
    argv = hook_argv(command)
    if not argv or len(argv) != 8 or argv[2::2] != ['--repo', '--host', '--event']:
        return False
    if not marked and not Path(argv[0]).name.lower().startswith('python'):
        return False
    script = Path(argv[1])
    if argv[5] not in ('claude', 'codex') or argv[7] not in HOOK_EVENTS:
        return False
    if script.name != 'coordination_hook.py':
        return False
    managed = (script.parent.name == 'bin' and script.parent.parent.name == '.hglos') or (
        script.parent.name == 'scripts' and script.parent.parent.name in ('hglos-coordinate', 'hglos'))
    if not managed:
        return False
    return root is None or Path(argv[3]).resolve() == Path(root).resolve()


def merge_hooks(existing, additions, replaced=None, root=None):
    """Add HGLOS groups; earlier HGLOS handlers of this project (any version/path) are replaced, user hooks kept."""
    result = copy.deepcopy(existing)
    hooks = result.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise CoordinationError('Existing hooks field is not an object')
    for event in list(hooks):
        target = hooks[event]
        if not isinstance(target, list):
            raise CoordinationError(f'Existing {event} hooks are not a list')
        for old in target:
            if not isinstance(old, dict) or not isinstance(old.get('hooks'), list):
                raise CoordinationError('Malformed existing hook group')
        wanted = additions.get(event, [])
        current = [h for group in target for h in group['hooks'] if ours(h, root)]
        if all(group in target for group in wanted) and len(current) == sum(len(g['hooks']) for g in wanted):
            continue  # Already exactly ours: keep position and bytes.
        if replaced is not None:
            replaced.extend({'event': event, 'command': h.get('command', '')} for h in current)
        kept = []
        for group in target:
            handlers = [h for h in group['hooks'] if not ours(h, root)]
            if handlers:
                kept.append(dict(group, hooks=handlers))
        hooks[event] = kept + copy.deepcopy(wanted)
    for event, groups in additions.items():
        hooks.setdefault(event, copy.deepcopy(groups))
    return result


def recorded(root):
    """Hashes of the managed copies written by the last --apply (empty if none/unreadable)."""
    record = root / BIN / MANIFEST
    no_links(record, root)
    try:
        data = json.loads(record.read_text(encoding='utf-8')) if record.exists() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def unmodified(path, previous, name, legacy=()):
    """A managed copy may be replaced only if its bytes match the recorded (or a known released) hash."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return previous.get(name) == digest or digest in legacy


def policy_diff(path, new):
    """Unified diff of a user-modified policy copy against the new policy, bounded for the console."""
    old = path.read_bytes().decode('utf-8', errors='replace').splitlines(keepends=True)
    lines = list(difflib.unified_diff(old, new.splitlines(keepends=True),
                                      fromfile=f'{path} (local)', tofile=f'{path.name} (new)'))
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [f'... {len(lines) - DIFF_LINES} more diff lines\n']
    return ''.join(line if line.endswith('\n') else line + '\n' for line in lines)


def pinned_outputs(root):
    """Byte-identical hook copies; refuse to clobber a locally edited copy. Returns (outputs, sources)."""
    folder = root / BIN
    no_links(folder, root)
    sources = {name: Path(__file__).with_name(name).read_bytes() for name in PINNED}
    previous = recorded(root)
    outputs = {}
    for name, content in sources.items():
        path = folder / name
        no_links(path, root)
        if path.exists() and path.read_bytes() != content and not unmodified(path, previous, name):
            raise CoordinationError(f'Pinned hook copy was modified locally; inspect {path} before replacement')
        outputs[path] = content
    return outputs, sources


def manifest_output(root, previous, managed):
    """Hash record of every managed copy written now, keeping earlier entries for copies not rewritten."""
    record = {name: value for name, value in previous.items() if isinstance(value, str) and len(value) == 64}
    record.update({name: hashlib.sha256(content).hexdigest() for name, content in managed.items()})
    path = root / BIN / MANIFEST
    no_links(path, root)
    return path, (json.dumps(dict(sorted(record.items())), indent=2) + '\n').encode('utf-8')


def install_plan(root, host, python, mode, replaced=None, replaced_policies=None):
    if host not in ('claude', 'codex', 'antigravity', 'both', 'all'):
        raise CoordinationError('Unknown coordination host')
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not Path(python).is_file():
        raise CoordinationError('Expected existing project directory and Python executable')
    no_links(root / '.hglos', root)
    outputs = {}
    policies = {}
    previous = recorded(root)
    for name in ('COORDINATION.md', 'RULES.md'):
        policy = (Path(__file__).parent.parent / name).read_text(encoding='utf-8')
        policy_path = root / '.hglos' / name
        no_links(policy_path, root)
        if policy_path.exists() and policy_path.read_bytes() != policy.encode('utf-8'):
            # Unmodified managed copies (manifest hash, or the pre-manifest release) are replaced;
            # a user-modified copy is shown and preserved.
            if not unmodified(policy_path, previous, name, LEGACY_POLICY_SHA256.get(name, ())):
                raise CoordinationError(f'Existing coordination policy differs; preserve local policy: {name}\n'
                                        + policy_diff(policy_path, policy))
            if replaced_policies is not None:
                replaced_policies.append(str(policy_path))
        outputs[policy_path] = policies[name] = policy
    if (root / MARKER).exists():
        config = json.loads((root / MARKER).read_text(encoding='utf-8'))
        if config.get('mode') != mode:
            raise CoordinationError('Changing a live coordination mode is unsupported')
    hosts = ('claude', 'codex', 'antigravity') if host == 'all' else (('claude', 'codex') if host == 'both' else (host,))
    # Policy hashes are recorded for every host (also Antigravity, which gets no hook adapter).
    managed = {name: text.encode('utf-8') for name, text in policies.items()}
    for target in hosts:
        if target in ('claude', 'codex'):
            copies, sources = pinned_outputs(root)
            outputs.update(copies)
            managed.update(sources)
            path = root / ('.codex/hooks.json' if target == 'codex' else '.claude/settings.local.json')
            no_links(path, root)
            existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if not isinstance(existing, dict):
                raise CoordinationError('Existing host settings must be an object')
            found = []
            merged = merge_hooks(existing, hooks_for(root, target, python), found, root)
            if replaced is not None:
                replaced.extend(dict(item, file=str(path)) for item in found)
            outputs[path] = json.dumps(merged, ensure_ascii=False, indent=2) + '\n'
        instruction = root / {'codex': 'AGENTS.md', 'claude': 'CLAUDE.md', 'antigravity': 'GEMINI.md'}[target]
        no_links(instruction, root)
        old = instruction.read_bytes().decode('utf-8') if instruction.exists() else ''
        if START in old or END in old:
            if old.count(START) != 1 or old.count(END) != 1 or BLOCK not in old:
                raise CoordinationError('Existing managed instruction block differs; inspect it first')
            outputs[instruction] = old
        else:
            outputs[instruction] = old + ('\n\n' if old else '') + BLOCK + '\n'
    path, record = manifest_output(root, previous, managed)
    outputs[path] = record
    return root, outputs


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--host', choices=('claude', 'codex', 'antigravity', 'both', 'all'), default='both')
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--mode', choices=('exclusive', 'scoped'), default='exclusive')
    parser.add_argument('--apply', action='store_true', help='Opt in to local hook/settings/instruction edits')
    args = parser.parse_args()
    try:
        replaced, policies = [], []
        root, outputs = install_plan(args.repo, args.host, args.python.resolve(), args.mode, replaced, policies)
        if args.apply:
            initialize(root, args.mode)
            for path, content in outputs.items():
                no_links(path, root)
                data = content if isinstance(content, bytes) else content.encode('utf-8')
                if path.exists() and path.read_bytes() == data:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                # Write a sibling temp file, then replace: a failure never leaves a truncated copy.
                fd, temp = tempfile.mkstemp(prefix='.hglos-setup-', dir=path.parent)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data)
                    os.replace(temp, path)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
        print(json.dumps({'applied': args.apply, 'mode': args.mode,
                          'files': [str(path) for path in outputs],
                          'replaced_hooks': replaced,
                          'replaced_policies': policies,
                          'manual_hosts': ['antigravity'] if args.host in ('antigravity', 'all') else [],
                          'required': 'Review/trust hooks in each host. Validate actual event coverage. '
                                      'Hooks run the pinned .hglos/bin copy; rerun setup --apply to update it. '
                                      'Antigravity uses instructions and manual CLI; no automatic hook blocking is installed for it. '
                                      'Codex callback failures/timeouts may not block; this is cooperative coordination, not an OS sandbox. '
                                      'Existing running sessions must load the hooks before participating.'}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
