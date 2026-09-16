"""Where the customer's greeting files live, and which one they used last.

Two problems, one place to solve them.

The first is that a customer should never be asked where to put a file. "Save
as" hands somebody who has never opened a terminal a directory tree, a file
name, an extension and a filter, and the usual outcome is a greeting list in
the Downloads folder under a name nobody will recognise next month. So the
window asks for a name and nothing else, and the file lands here. Everything
this program writes -- logs, the vision model, greetings -- is under one
directory in the customer's home, which is also the only directory it can
count on being writable: on Windows the program itself may well sit in
Program Files.

The second is that the file has to still be there tomorrow. The window used to
forget it the moment it closed, so a customer with a curated list of forty
greetings re-imported it every single time. The last file used is remembered
and reopened.

Names are the customer's, which means they have to be checked rather than
trusted: a name is one component, not a path, and Windows has opinions about
some of them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .i18n import t

SUFFIX = '.yaml'
LONGEST = 60

# Illegal in a Windows file name, and `/` on everything else. Checked rather
# than stripped: quietly turning "KLGW/2F" into "KLGW2F" saves a file the
# customer did not name and cannot find by searching for what they typed.
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows refuses these whatever the extension, and has since DOS.
RESERVED = {'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{d}' for d in range(1, 10)),
            *(f'LPT{d}' for d in range(1, 10))}


def directory() -> Path:
    """Where greeting files are kept."""
    return Path.home() / '.x2-deployer' / 'phrases'


def _pointer() -> Path:
    return Path.home() / '.x2-deployer' / 'last-phrases.txt'


def safe_name(text: str) -> str:
    """The customer's name for a file, checked. Raises with a sentence."""
    name = (text or '').strip()
    if name.lower().endswith(('.yaml', '.yml')):
        name = name.rsplit('.', 1)[0].strip()

    if not name:
        raise ValueError(t('library.name_empty'))
    if FORBIDDEN.search(name):
        raise ValueError(t('library.name_symbols'))
    # A name of dots is how `..` gets in, and `.` names hide the file.
    if set(name) <= {'.'} or name.startswith('.'):
        raise ValueError(t('library.name_dots'))
    if name.upper() in RESERVED:
        raise ValueError(t('library.name_reserved', name=name))
    if len(name) > LONGEST:
        raise ValueError(t('library.name_long', limit=LONGEST))
    # Windows silently drops a trailing dot or space, so the file would not be
    # where the name says it is.
    if name[-1] in '. ':
        raise ValueError(t('library.name_trailing'))
    return name


def path_for(name: str) -> Path:
    """Where a name of the customer's lands. Never outside `directory()`."""
    target = directory() / (safe_name(name) + SUFFIX)
    # Belt and braces: safe_name already refuses separators, and this is what
    # makes that a guarantee rather than a hope.
    if target.parent != directory():
        raise ValueError(t('library.name_symbols'))
    return target


def saved() -> List[Path]:
    """Every greeting file kept here, most recently written first."""
    if not directory().is_dir():
        return []
    return sorted(directory().glob(f'*{SUFFIX}'),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def remember(path) -> None:
    """Note this as the file in use. Never raises: this is a convenience."""
    try:
        pointer = _pointer()
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(Path(path).resolve()), encoding='utf-8')
    except OSError:
        pass


def last() -> Optional[Path]:
    """The file used last, if it is still there."""
    try:
        recorded = _pointer().read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not recorded:
        return None
    path = Path(recorded)
    return path if path.is_file() else None


def forget() -> None:
    _pointer().unlink(missing_ok=True)


def start_directory() -> Path:
    """Where an open dialog should land.

    Beside the file they used last, or in this program's own folder once it
    holds something, or the home directory. Never inside the running program:
    frozen, that is the temporary directory it unpacked itself into, which is
    full of files that look like greeting lists, belong to the program, and
    cease to exist when it closes.
    """
    previous = last()
    if previous is not None:
        return previous.parent
    if saved():
        return directory()
    return Path.home()
