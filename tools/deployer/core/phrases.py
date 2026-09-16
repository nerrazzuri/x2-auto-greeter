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
from dataclasses import dataclass
from typing import List, Optional, Sequence

import yaml

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
    message: str
    fixable: bool              # True when apply_fixes() can repair it

    def __str__(self) -> str:
        return f'第 {self.line} 句:{self.message}'


def _clean(text: str) -> str:
    for bad, good in SUBSTITUTIONS.items():
        text = text.replace(bad, good)
    return LEADING_NUMBER.sub('', text).strip()


def check(phrases: Sequence[str]) -> List[Problem]:
    """Every problem in the list, in the order a reader would meet them."""
    problems: List[Problem] = []
    seen = {}

    if not [p for p in phrases if p.strip()]:
        return [Problem(0, '', '一句问候语都没有,机器人不会说话', False)]

    for index, raw in enumerate(phrases, start=1):
        text = raw.strip()
        if not text:
            problems.append(Problem(index, raw, '这一行是空的', True))
            continue

        bad = sorted({c for c in text if c in SUBSTITUTIONS})
        if bad:
            problems.append(Problem(
                index, raw,
                f'含有 TTS 读不出的字符 {" ".join(bad)},多半是从 Word 粘贴来的',
                True))

        if LEADING_NUMBER.match(raw):
            problems.append(Problem(
                index, raw, '开头的编号会被一起念出来', True))

        markup = sorted(set(MARKUP.findall(text)))
        if markup:
            problems.append(Problem(
                index, raw,
                f'含有会被念出来的符号 {" ".join(markup)}', False))

        if len(text) > MAX_CHARS:
            problems.append(Problem(
                index, raw,
                f'太长了({len(text)} 字,上限 {MAX_CHARS}),说完人已经走了',
                False))

        key = text.lower()
        if key in seen:
            problems.append(Problem(
                index, raw, f'和第 {seen[key]} 句重复', False))
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


# Two files in this repository are named for a site, and only one of them is a
# greeting list. The other is the deployment's settings, and it is the one a
# customer reaches for first, because it is called klgw.yaml while the
# greetings are called phrases-klgw.yaml. Saying "there is no phrases: section"
# to someone who has never seen YAML tells them nothing, so the message names
# what they opened and what to open instead.
SETTINGS_KEYS = {'backend', 'camera', 'speech', 'presence', 'detect', 'gestures'}


def _wrong_file(path: str, document) -> str:
    name = os.path.basename(path)
    if isinstance(document, dict) and SETTINGS_KEYS & set(document):
        return (f'{name} 是部署设置文件,不是问候语。'
                f'问候语文件的名字通常以 phrases- 开头,'
                f'例如 phrases-klgw.yaml。')
    return (f'{name} 里没有问候语。问候语文件应该是这样的:\n'
            f'phrases:\n'
            f'  - "第一句"\n'
            f'  - "第二句"\n'
            f'也可以直接用记事本写一个 .txt,一句一行。')


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
                f'{os.path.basename(path)} 里的 "phrases:" 下面应该是一句一行的'
                f'列表,现在不是。')
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
