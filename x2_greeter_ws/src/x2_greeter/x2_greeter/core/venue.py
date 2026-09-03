"""What kind of place the robot is standing in, and what it may say about it.

The camera answers "what is in front of me". It cannot answer "does this shop
take returns" -- so everything the robot asserts as fact about the venue comes
from a profile a human wrote, and everything else is deflected to a human.
The profile is the authority: where it and the frame disagree, the profile
wins.

A malformed profile raises rather than degrading, because the degraded form of
this module is a robot that answers venue questions from the language model's
imagination.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Tuple

import yaml

from x2_greeter.core.language import ALLOWED_LANGUAGES, normalise_language

_REQUIRED = ('kind', 'role', 'opening', 'facts', 'topics_forbidden',
             'deflect_to_human')


class VenueError(Exception):
    """A venue profile is missing, unreadable, or incomplete."""


@dataclass(frozen=True)
class VenueProfile:
    kind: str
    role: str
    language_default: str
    opening: Dict[str, str]
    facts: Tuple[str, ...]
    topics_encouraged: Tuple[str, ...]
    topics_forbidden: Tuple[str, ...]
    deflect_to_human: str

    def opening_for(self, language: str) -> str:
        lang = normalise_language(language) or self.language_default
        return self.opening.get(lang) or self.opening[self.language_default]

    def to_prompt(self) -> str:
        """Render the profile as the venue block of the dialogue system prompt."""
        lines = [
            f'You are {self.role}.',
            '',
            'Facts you may state. These are the ONLY things you may assert about '
            'this place; anything else about the venue you do not know:',
        ]
        lines += [f'- {fact}' for fact in self.facts]
        if self.topics_encouraged:
            lines += ['', 'Good things to talk about:']
            lines += [f'- {topic}' for topic in self.topics_encouraged]
        lines += ['', 'Never discuss these, even if asked directly:']
        lines += [f'- {topic}' for topic in self.topics_forbidden]
        lines += ['',
                  'When asked something you cannot answer from the facts above, '
                  f'say: "{self.deflect_to_human}"']
        return '\n'.join(lines)


def parse_venue(doc: Mapping) -> VenueProfile:
    if not isinstance(doc, Mapping) or not isinstance(doc.get('venue'), Mapping):
        raise VenueError("venue profile must have a top-level 'venue' mapping")
    venue = doc['venue']

    missing = [key for key in _REQUIRED if key not in venue]
    if missing:
        raise VenueError(f'venue profile is missing required key(s): '
                         f'{", ".join(missing)}')

    language_default = normalise_language(venue.get('language_default', 'en'))
    if language_default is None:
        raise VenueError(
            f'venue.language_default must be one of {ALLOWED_LANGUAGES}, got '
            f'{venue.get("language_default")!r}')

    opening = venue['opening']
    if not isinstance(opening, Mapping) or not opening.get(language_default):
        raise VenueError(
            f'venue.opening must provide a line for the default language '
            f'{language_default!r}')

    facts = tuple(str(fact) for fact in venue['facts'])
    if not facts:
        raise VenueError('venue.facts must list at least one fact: an empty '
                         'list produces a robot that invents them')

    forbidden = tuple(str(topic) for topic in venue['topics_forbidden'])
    if not forbidden:
        raise VenueError('venue.topics_forbidden must list at least one topic')

    return VenueProfile(
        kind=str(venue['kind']),
        role=str(venue['role']),
        language_default=language_default,
        opening={str(k): str(v) for k, v in opening.items()},
        facts=facts,
        topics_encouraged=tuple(str(t) for t in venue.get('topics_encouraged', ())),
        topics_forbidden=forbidden,
        deflect_to_human=str(venue['deflect_to_human']),
    )


def load_venue(path) -> VenueProfile:
    path = Path(path)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            doc = yaml.safe_load(handle)
    except OSError as exc:
        raise VenueError(f'could not read venue profile {path}: {exc}') from exc
    except yaml.YAMLError as exc:
        raise VenueError(f'venue profile {path} is not valid YAML: {exc}') from exc
    return parse_venue(doc)
