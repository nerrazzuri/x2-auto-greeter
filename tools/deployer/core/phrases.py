"""Reading, writing and checking a customer's greeting list.

The deployer exists because some customers have no engineer, so this module
assumes the person editing these lines has never seen YAML and will never see
a traceback. Two things follow from that.

First, it reads more than it writes. A list of sentences arrives as a .txt
someone typed in Notepad, or as the .yaml a previous deployment produced, and
either is accepted. It only ever writes YAML, because that is what the robot's
CannedBackend loads.

Second, every way of getting it wrong is reported as a sentence, against the
line it happened on, rather than raised. A customer who pastes curly quotes out
of Word has made an ordinary mistake, and the deployer's job is to point at the
line and say what to change -- not to fail at the robot twenty minutes later,
which is what happens today: the node logs one warning nobody reads and greets
the mall in the built-in English jokes instead of the customer's script.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Sequence

import yaml

from .i18n import t

# The vendor TTS reads punctuation it does not recognise out loud rather than
# skipping it, so a curly quote pasted from Word becomes an audible stumble in
# a shopping mall. Each is mapped to what the customer meant.
SUBSTITUTIONS = {
    '‘': "'", '’': "'",          # curly single quotes
    '“': '"', '”': '"',          # curly double quotes
    '–': ',', '—': ',',          # en dash, em dash
    '…': '...',                       # ellipsis
    ' ': ' ',                         # non-breaking space
}

# Markdown and list numbering survive copy-paste out of a brief and are read
# aloud verbatim.
MARKUP = re.compile(r'[*_#`|<>{}\[\]]')
# The lookahead keeps a decimal out of trouble: "1. Welcome" is numbering,
# "1.5 metres from me" is a sentence. Chinese lists write "3、" with no
# space after, so the separator cannot be required to have one.
LEADING_NUMBER = re.compile(r'^\s*\d+\s*[.)、]\s*(?=\D)')

# Long enough for the client script's longest line (191 characters) with room
# to spare; past this a greeting outlasts the person walking by.
MAX_CHARS = 260


@dataclass(frozen=True)
class Problem:
    """Something wrong with one phrase, in words the customer can act on."""

    line: int                  # 1-based, as shown in the editor
    text: str
    key: str                   # an i18n key, not a sentence -- see i18n.py
    params: dict
    fixable: bool              # True when apply_fixes() can repair it

    @property
    def message(self) -> str:
        return t(self.key, **self.params)

    def __str__(self) -> str:
        return t('problem.line', line=self.line, message=self.message)


def _clean(text: str) -> str:
    for bad, good in SUBSTITUTIONS.items():
        text = text.replace(bad, good)
    return LEADING_NUMBER.sub('', text).strip()


def check(phrases: Sequence[str]) -> List[Problem]:
    """Every problem in the list, in the order a reader would meet them."""
    problems: List[Problem] = []
    seen = {}

    if not [p for p in phrases if p.strip()]:
        return [Problem(0, '', 'problem.empty_list', {}, False)]

    for index, raw in enumerate(phrases, start=1):
        text = raw.strip()
        if not text:
            problems.append(Problem(index, raw, 'problem.blank', {}, True))
            continue

        bad = sorted({c for c in text if c in SUBSTITUTIONS})
        if bad:
            problems.append(Problem(
                index, raw, 'problem.bad_chars', {'chars': ' '.join(bad)}, True))

        if LEADING_NUMBER.match(raw):
            problems.append(Problem(
                index, raw, 'problem.numbering', {}, True))

        markup = sorted(set(MARKUP.findall(text)))
        if markup:
            problems.append(Problem(
                index, raw, 'problem.markup',
                {'chars': ' '.join(markup)}, False))

        if len(text) > MAX_CHARS:
            problems.append(Problem(
                index, raw, 'problem.too_long',
                {'length': len(text), 'limit': MAX_CHARS}, False))

        key = text.lower()
        if key in seen:
            problems.append(Problem(
                index, raw, 'problem.duplicate', {'line': seen[key]}, False))
        else:
            seen[key] = index

    return problems


def apply_fixes(phrases: Sequence[str]) -> List[str]:
    """Repair what can be repaired: quotes, dashes, numbering, blank lines.

    Returns a new list. What it cannot fix -- markup, over-long lines,
    duplicates -- it leaves alone for check() to report, because each of those
    needs a decision the customer has to make.
    """
    cleaned = [_clean(p) for p in phrases]
    return [p for p in cleaned if p]


# A customer looking for their greetings will sooner or later open something
# else: the robot's settings, the shipped config, a note they wrote. Saying
# "there is no phrases: section" to someone who has never seen YAML tells them
# nothing about what they did, so the message names the file they opened and
# says what a greeting file looks like instead.
SETTINGS_KEYS = {'backend', 'camera', 'speech', 'presence', 'detect', 'gestures'}


def _wrong_file(path: str, document) -> str:
    name = os.path.basename(path)
    if isinstance(document, dict) and SETTINGS_KEYS & set(document):
        return t('problem.not_greetings', name=name)
    return t('problem.no_phrases', name=name)


def default_path() -> Optional[str]:
    """The sample greeting list that ships with the deployer, if it is here.

    Venue-neutral on purpose: the KL Gateway Mall list names a mall and a
    company, and handing those to a different customer as their starting point
    would be worse than handing them nothing.
    """
    import sys

    bundle = getattr(sys, '_MEIPASS', None)
    roots = ([Path(bundle) / 'payload'] if bundle else
             [Path(__file__).resolve().parents[3]])
    for root in roots:
        candidate = (root / 'x2_greeter_ws' / 'src' / 'x2_greeter' /
                     'config' / 'phrases.yaml')
        if candidate.is_file():
            return str(candidate)
    return None


def defaults() -> List[str]:
    """The sample list, or nothing if the deployer was unpacked without it."""
    path = default_path()
    if path is None:
        return []
    try:
        return load(path)
    except (OSError, ValueError):
        return []


def load(path) -> List[str]:
    """Read a phrase list from .yaml or .txt.

    .txt is one phrase per line, `#` comments and blank lines ignored -- the
    format someone can produce in Notepad without knowing anything at all.
    """
    path = os.fspath(path)
    with open(path, 'r', encoding='utf-8-sig') as handle:
        raw = handle.read()

    if path.lower().endswith(('.yaml', '.yml')):
        document = yaml.safe_load(raw) or {}
        if not isinstance(document, dict) or 'phrases' not in document:
            raise ValueError(_wrong_file(path, document))
        items = document.get('phrases') or []
        if not isinstance(items, list):
            raise ValueError(
                t('problem.phrases_not_list', name=os.path.basename(path)))
        return [str(item).strip() for item in items if str(item).strip()]

    return [line.strip() for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def save(path, phrases: Sequence[str], venue: Optional[str] = None) -> None:
    """Write the list as the YAML the robot loads, header and all.

    Always double-quoted: apostrophes are everywhere in English greetings
    ("I'm", "don't", "you're") and a single-quoted YAML scalar containing one
    is a parse error. That single trap accounts for most of the ways a
    hand-written phrase file fails.
    """
    lines = ['# Greeting phrases, generated by the X2 deployer.']
    if venue:
        lines.append(f'# Venue: {venue}')
    lines += [
        '#',
        '# One is picked at random per greeting. Order does not matter, and a',
        '# line may be added or removed at any time. Edit through the deployer',
        '# rather than by hand: it checks for the characters the robot cannot',
        '# say out loud.',
        'phrases:',
    ]
    for phrase in phrases:
        text = _clean(str(phrase))
        if not text:
            continue
        lines.append('  - "{}"'.format(text.replace('\\', '\\\\').replace('"', '\\"')))

    with open(os.fspath(path), 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
