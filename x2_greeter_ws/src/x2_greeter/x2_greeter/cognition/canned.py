"""The offline backend: pre-written phrases, no network, never fails.

This is the floor of the system. Whatever else breaks, the robot still greets.
"""
from __future__ import annotations

import os
import random
from typing import Optional, Sequence

import yaml

from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

# Order is load-bearing: tools/make_greeting_audio.py records phrase N as
# greeting_{N:02d}.wav. Append freely; never reorder or delete.
DEFAULT_PHRASES = (
    'Hello there! Nice to see you.',
    'Hi! Welcome. I am X2.',
    'Good to see you. How are you doing?',
    'Hey! Thanks for stopping by.',
    'Hello! I am X2, pleased to meet you.',
    'Hi there! Great to have you here.',
)


def load_phrases(path) -> tuple:
    """Read the shared phrase list from config/phrases.yaml."""
    with open(os.fspath(path), 'r', encoding='utf-8') as handle:
        document = yaml.safe_load(handle) or {}
    phrases = tuple(str(p) for p in (document.get('phrases') or []) if str(p).strip())
    if not phrases:
        raise ValueError(f'{path} must list at least one phrase under "phrases"')
    return phrases


class CannedBackend:
    """Returns a pre-written greeting and lets the selector pick the gesture.

    It never inspects the frame — offline, the local detector has already
    decided somebody is there, and second-guessing that would only mean
    refusing to greet.
    """

    name = 'canned'

    def __init__(self, phrases: Sequence[str] = DEFAULT_PHRASES,
                 rng: Optional[random.Random] = None) -> None:
        phrases = tuple(phrases)
        if not phrases:
            raise ValueError('CannedBackend needs at least one phrase')
        self._phrases = phrases
        self._rng = rng if rng is not None else random.Random()
        self._last: Optional[str] = None

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        greeting = self._next_phrase()
        return Verdict(
            person_present=True,
            facing_robot=False,   # unknowable locally, and never a gate
            confidence=0.0,
            greeting=greeting,
            gesture=None,         # let GestureSelector choose at random
            reason='offline canned greeting',
            source=self.name,
        )

    def _next_phrase(self) -> str:
        pool = [p for p in self._phrases if p != self._last]
        if not pool:
            pool = list(self._phrases)
        chosen = self._rng.choice(pool)
        self._last = chosen
        return chosen
