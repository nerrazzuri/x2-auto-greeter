#!/usr/bin/env python3
"""Apply a deployment profile from tools/deploy/sites/ to a robot's site.yaml.

    apply_site.py SITE_YAML PROFILE_YAML [--share DIR] [--root DIR]

Line-based, not a YAML round-trip. site.yaml is where a robot's bring-up
history is written down -- which flags have been proven on this machine, and
why -- and yaml.safe_load followed by safe_dump would throw every one of those
comments away. So each key is rewritten in place, inside its own block, and
everything else in the file is left byte for byte as it was.

Idempotent: running it twice changes nothing the second time. It backs the file
up once per day before writing, and refuses to write a file it cannot parse
afterwards.
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys

import yaml


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def set_in_block(lines: list, block: str, key: str, value: str):
    """Set block.key, returning a one-line report, or None if already set.

    Scoped to the named block so a key that also exists elsewhere in the file
    (`enabled`, `source` and `tier` all appear more than once) is never the one
    that gets rewritten by accident.
    """
    for i, line in enumerate(lines):
        if not re.match(rf'^\s*{re.escape(block)}:\s*(#.*)?$', line):
            continue
        base = indent_of(line)
        child = None
        j = i + 1
        while j < len(lines):
            current = lines[j]
            if current.strip() and not current.lstrip().startswith('#'):
                if indent_of(current) <= base:
                    break
                if child is None:
                    child = indent_of(current)
                match = re.match(rf'^(\s*){re.escape(key)}:(\s*)(.*)$', current)
                if match and indent_of(current) == child:
                    was = match.group(3).strip()
                    if was == value:
                        return None
                    lines[j] = f'{match.group(1)}{key}: {value}\n'
                    return f'{block}.{key}: {was} -> {value}'
            j += 1
        pad = ' ' * (child if child is not None else base + 2)
        lines.insert(i + 1, f'{pad}{key}: {value}\n')
        return f'{block}.{key}: (absent) -> {value}'
    raise SystemExit(f'no "{block}:" block to put {key} in; nothing written')


def as_scalar(value) -> str:
    """One YAML scalar, quoted the way the rest of site.yaml quotes things."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    return text if re.fullmatch(r'[A-Za-z0-9_.\-]+', text) else f"'{text}'"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('site')
    parser.add_argument('profile')
    parser.add_argument('--share', default='',
                        help="replaces @SHARE@ in the profile's values")
    parser.add_argument('--root', default='',
                        help="replaces @ROOT@ in the profile's values")
    # One more key, from the command line rather than the file. The phrase
    # list is the only value that differs between customers, and appending it
    # to the profile as text would write a second `speech:` block -- which
    # YAML resolves by keeping the last one, silently dropping speech.tier.
    parser.add_argument('--set', action='append', default=[],
                        metavar='BLOCK.KEY=VALUE',
                        help='set one more key, e.g. speech.phrases_file=/x.yaml')
    args = parser.parse_args(argv)

    with open(args.profile, encoding='utf-8') as handle:
        profile = yaml.safe_load(handle) or {}

    for pair in args.set:
        dotted, _, value = pair.partition('=')
        block, _, key = dotted.partition('.')
        if not block or not key or not value:
            raise SystemExit(f'--set 要写成 BLOCK.KEY=VALUE,收到的是 {pair!r}')
        profile.setdefault(block, {})[key] = value

    with open(args.site, encoding='utf-8') as handle:
        lines = handle.readlines()

    wanted = {}
    reports = []
    for block, keys in profile.items():
        if not isinstance(keys, dict):
            raise SystemExit(f'{args.profile}: "{block}" must be a block of keys')
        for key, value in keys.items():
            if isinstance(value, str):
                value = value.replace('@SHARE@', args.share).replace('@ROOT@', args.root)
            wanted[(block, key)] = value
            report = set_in_block(lines, block, key, as_scalar(value))
            if report:
                reports.append(report)

    text = ''.join(lines)
    params = next(iter(yaml.safe_load(text).values()))['ros__parameters']
    for (block, key), value in wanted.items():
        got = params.get(block, {}).get(key)
        if got != value:
            raise SystemExit(
                f'refusing to write: {block}.{key} reads back as {got!r}, '
                f'not {value!r}')

    if reports:
        backup = f'{args.site}.bak-{datetime.date.today():%Y%m%d}'
        try:
            with open(backup, 'x'):
                pass
            shutil.copy2(args.site, backup)
            print(f'   backup -> {backup}')
        except FileExistsError:
            pass
        with open(args.site, 'w', encoding='utf-8') as handle:
            handle.write(text)
        for report in reports:
            print(f'   {report}')
    else:
        print('   already applied; nothing changed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
