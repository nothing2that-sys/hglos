#!/usr/bin/env python3
"""Install self-contained skills without editing host settings or existing skills."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import uuid

from package_skill import SKILLS, entries, walk

TARGETS = ('claude', 'codex', 'antigravity', 'antigravity-cli')


def no_links(path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Refusing symlink/junction in installation path: {parent}")
    return path


def destination(target, project=None, user=False, skills_dir=None):
    if target not in TARGETS:
        raise ValueError(f'Unknown individual host: {target}')
    if skills_dir is not None:
        return no_links(Path(skills_dir).expanduser())
    if project is not None:
        root = no_links(Path(project).expanduser())
        if not root.is_dir():
            raise ValueError(f"Project directory does not exist: {root}")
        return root / (".claude" if target == "claude" else ".agents") / "skills"
    if not user:
        raise ValueError("Choose --project, --user, or --skills-dir")
    roots = {
        "codex": Path.home() / ".agents" / "skills",
        "antigravity": Path.home() / ".gemini" / "config" / "skills",
        "antigravity-cli": Path.home() / ".gemini" / "antigravity-cli" / "skills",
        "claude": Path.home() / ".claude" / "skills",
    }
    return roots[target]


def destinations(target, project=None, user=False, skills_dir=None):
    if target == 'all' and skills_dir is not None:
        raise ValueError('--target all requires --project or --user; select one host for --skills-dir')
    hosts = TARGETS if target == 'all' else (target,)
    return list(dict.fromkeys(destination(host, project, user, skills_dir) for host in hosts))


def install_plan(dest):
    """Read all source and destination bytes without creating any directories."""
    dest = no_links(dest)
    for folder in (dest, *dest.parents):
        if folder.exists() and not folder.is_dir():
            raise ValueError(f'Installation path must be a directory: {folder}')
    pending = []
    # Inspect every skill before writing any of them; never replace local edits.
    for skill in SKILLS:
        expected = {Path(arc).relative_to(skill): src.read_bytes() for arc, src in entries(skill)}
        target = dest / skill
        no_links(target)
        if target.is_symlink():
            raise ValueError(f"Refusing linked skill directory: {target}")
        if target.exists():
            if not target.is_dir() or any(p.is_symlink() or getattr(p.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT for p in target.rglob("*")):
                raise ValueError(f"Conflicting skill: {target}")
            actual = {p: (target / p).read_bytes() for p in walk(target)}
            if actual != expected:
                raise ValueError(f"Existing skill differs; keep/rename it before installing: {target}")
            continue
        pending.append((target, expected))
    return pending


def lock_path(dest):
    """Lock file beside (not inside) a skills folder, shared by every HGLOS install/upgrade of it."""
    dest = Path(os.path.abspath(dest))
    return dest.parent / f'.{dest.name}.hglos-install.lock'


@contextmanager
def install_lock(roots):
    """Serialise installers per skills folder: exclusive-create a lock file, refuse if one exists.

    Installers never run their check-then-change steps concurrently on one folder, so a rollback
    cannot race another installer. Only locks this call created are removed, also on interrupt.
    """
    held = []
    try:
        for dest in dict.fromkeys(roots):
            path = lock_path(dest)
            no_links(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise ValueError(f'Another HGLOS install/upgrade is in progress for {dest} (lock: {path}). '
                                 'If no installer is running, delete that lock file and retry.') from None
            held.append(path)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(f'pid {os.getpid()} since {time.strftime("%Y-%m-%dT%H:%M:%S")}\n')
        yield
    finally:
        for path in reversed(held):
            try:
                path.unlink()
            except OSError:
                pass


def install_many(roots, dry_run=False):
    roots = list(dict.fromkeys(roots))  # A one-shot iterable must survive preflight and the locked pass.
    # All host locations must pass preflight before the first host is written.
    pending = []
    for dest in roots:
        pending.extend(install_plan(dest))
    if dry_run:
        return [str(target) for target, _ in pending]
    with install_lock(roots):
        # Decide under the lock - including "nothing to do" - so an in-flight installer is never missed.
        pending = []
        for dest in roots:
            pending.extend(install_plan(dest))
        write_pending(pending)
    return [str(target) for target, _ in pending]


def write_pending(pending):
    for target, expected in pending:
        dest = target.parent
        no_links(dest)
        dest.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".hglos-install-", dir=dest))
        try:
            for rel, content in expected.items():
                path = stage / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            # Same filesystem rename. A concurrently created directory is not replaced.
            if target.exists():
                raise ValueError(f"Destination appeared during install: {target}")
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def install(dest, dry_run=False):
    return install_many([dest], dry_run)


def upgrade_plan(dest):
    """Skills whose installed bytes differ from (or are missing in) this release. Writes nothing."""
    dest = no_links(dest)
    for folder in (dest, *dest.parents):
        if folder.exists() and not folder.is_dir():
            raise ValueError(f'Installation path must be a directory: {folder}')
    changes = []
    for skill in SKILLS:
        expected = {Path(arc).relative_to(skill): src.read_bytes() for arc, src in entries(skill)}
        target = dest / skill
        no_links(target)
        if target.is_symlink():
            raise ValueError(f"Refusing linked skill directory: {target}")
        if not target.exists():
            changes.append((target, expected, False))
            continue
        if not target.is_dir() or any(p.is_symlink() or getattr(p.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT for p in target.rglob("*")):
            raise ValueError(f"Conflicting skill: {target}")
        if {p: (target / p).read_bytes() for p in walk(target)} != expected:
            changes.append((target, expected, True))
    return changes


def backup_folder(dest):
    """Beside (not inside) the skills root, so hosts never discover backups as skills."""
    folder = dest.parent / 'hglos-skill-backups' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    no_links(folder)
    folder.mkdir(parents=True)
    return folder


def identity(path):
    """File-system identity of a directory; a same-volume rename keeps it. None if the platform gives none."""
    info = os.stat(path)
    return (info.st_dev, info.st_ino) if info.st_ino else None


def restore(done):
    """Undo swaps newest first, deciding from the real paths; return what could not be undone.

    Each (target, backup, ours) is registered BEFORE its renames, so an interruption right after either
    rename is still covered. `ours` is the staging folder's identity: something at target is removed only
    if it IS that folder - never a folder another process created there meanwhile (or when no identity
    is available). backup None = the skill was missing before. A present backup means the old copy was
    moved away; an absent one means the move never happened and target is still the original.
    """
    problems = []
    for target, backup, ours in reversed(done):
        try:
            mine = target.exists() and ours is not None and identity(target) == ours
            if backup is None:
                if mine:
                    shutil.rmtree(target)
                elif target.exists() and ours is None:
                    raise OSError('new copy left in place: folder identity unavailable, not removed')
                elif target.exists():
                    # The skill was absent before; that state cannot be restored while another folder sits there.
                    raise OSError('was absent before; now occupied by a folder this upgrade did not create (kept)')
            elif backup.exists():
                if mine:
                    shutil.rmtree(target)
                if target.exists():
                    raise OSError(f'occupied by a folder this upgrade did not create; old copy kept at {backup}')
                os.rename(backup, target)
        except OSError as exc:
            problems.append(f'{target}: {exc}')
    return problems


def upgrade_many(roots, dry_run=False):
    """Back up each changed skill, then swap it in by rename. Any failure restores every root."""
    roots = list(dict.fromkeys(roots))  # A one-shot iterable must survive preflight and the locked pass.
    plans = [(dest, upgrade_plan(dest)) for dest in roots]
    if dry_run:
        return [str(target) for _, changes in plans for target, _, _ in changes], []
    with install_lock(roots):
        # Decide under the lock - including "nothing to do" - so an in-flight installer is never missed.
        plans = [(dest, upgrade_plan(dest)) for dest in roots]
        if not any(changes for _, changes in plans):
            return [], []
        return swap_all(plans)


def swap_all(plans):
    done, backups = [], {}
    try:
        for dest, changes in plans:
            if changes:
                no_links(dest)
                dest.mkdir(parents=True, exist_ok=True)
            for target, expected, exists in changes:
                stage = Path(tempfile.mkdtemp(prefix=".hglos-install-", dir=dest))
                try:
                    for rel, content in expected.items():
                        path = stage / rel
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(content)
                    backup = None
                    if exists:
                        if dest not in backups:
                            backups[dest] = backup_folder(dest)
                        backup = backups[dest] / target.name
                    elif target.exists():
                        raise ValueError(f"Destination appeared during install: {target}")
                    # Register before any rename: restore() reads the real paths, so an interruption
                    # landing right after either rename is still undone; the stage identity marks our copy.
                    done.append((target, backup, identity(stage)))
                    if backup is not None:
                        os.rename(target, backup)
                    os.rename(stage, target)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
    except BaseException as exc:
        # Any interruption (including Ctrl+C) after a backup move must still put every skill back.
        problems = restore(done)
        for folder in backups.values():
            for empty in (folder, folder.parent):
                if empty.is_dir() and not any(empty.iterdir()):
                    empty.rmdir()
        if not isinstance(exc, (OSError, ValueError)):
            if problems:
                kept = ', '.join(str(f) for f in backups.values())
                print(f'Upgrade interrupted; restore incomplete: {"; ".join(problems)}; backups kept: {kept}',
                      file=sys.stderr)
            raise
        if problems:
            kept = ', '.join(str(f) for f in backups.values())
            raise ValueError(f'Upgrade failed ({exc}); restore incomplete: {"; ".join(problems)}; backups kept: {kept}') from exc
        raise ValueError(f'Upgrade failed; every skill was restored: {exc}') from exc
    return [str(target) for target, _, _ in done], [str(folder) for folder in backups.values()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, choices=[*TARGETS, 'all'])
    location = ap.add_mutually_exclusive_group(required=True)
    location.add_argument("--project", type=Path)
    location.add_argument("--user", action="store_true")
    location.add_argument("--skills-dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upgrade", action="store_true",
                    help="Replace changed installed skills after backing them up; restores all on failure")
    args = ap.parse_args()
    try:
        roots = destinations(args.target, args.project, args.user, args.skills_dir)
        if args.upgrade:
            changed, backups = upgrade_many(roots, args.dry_run)
        else:
            changed = install_many(roots, args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.upgrade:
        for dest in roots:
            print(f"{'Upgrade preview' if args.dry_run else 'Upgraded'}: {dest}")
        print(f"Changed skills: {len(changed)}")
        for path in changed:
            print(path)
        for folder in backups:
            print(f"Backup: {folder}")
        return 0
    for dest in roots:
        print(f"{'Preview' if args.dry_run else 'Installed'}: {dest}")
    print(f"New skills: {len(changed)}; identical skills kept: {len(SKILLS) * len(roots) - len(changed)}")
    print("File installation only. Verify discovery in the host; merge RULES.md into project instructions manually.")
    for path in changed:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
