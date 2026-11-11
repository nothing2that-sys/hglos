"""Build a clean MIT source release from a reviewed explicit file allowlist."""
import argparse
import json
from pathlib import Path
import sys

from install_skills import no_links
import package_skill

REPO = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).with_name('public-release-files.json')


def entries():
    mapping = json.loads(MANIFEST.read_text(encoding='utf-8'))
    items = []
    for arc, name in mapping.items():
        relative = Path(arc)
        source_name = Path(name)
        if (relative.is_absolute() or source_name.is_absolute() or
                '..' in relative.parts or '..' in source_name.parts or '\\' in arc or
                relative.parts[0] in ('.git', 'validation', 'codex', 'doc', 'hglos-design')):
            raise ValueError(f'Not a reviewed public path: {arc}')
        source = no_links(REPO / source_name)
        if not source.is_file() or not source.resolve().is_relative_to(REPO.resolve()):
            raise ValueError(f'Unavailable public source: {source_name}')
        items.append((arc, source))
    # Every file actually needed by the maintained skills must be in the clean source archive.
    shipped = {path.resolve() for _, path in items}
    needed = {path.resolve() for skill in package_skill.SKILLS for _, path in package_skill.entries(skill)}
    if not needed <= shipped:
        raise ValueError('Update the reviewed allowlist for new maintained skill files')
    return sorted(items)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, default=REPO / 'skill')
    args = parser.parse_args()
    try:
        items = entries()
        output = package_skill.build('hglos-public-source', items, args.outdir)
        output = output.replace(output.with_suffix('.zip'))
        problems = package_skill.verify(output, items)
        if problems:
            raise ValueError('; '.join(problems))
    except (OSError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    print(f'Public source: {output} ({len(items)} reviewed files)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
