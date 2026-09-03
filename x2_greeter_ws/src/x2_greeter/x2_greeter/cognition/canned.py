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
    'Hello there! I was beginning to think nobody would walk past today.',
    'Well, hello! You are easily the most interesting thing I have seen all morning.',
    'Hi there! I am X2. I stand here, I look friendly, and occasionally I wave.',
    'Oh, hello! You have caught me practising my very best posture.',
    'Hello! I have been standing here doing nothing important at all. You are a welcome interruption.',
    'Hi! I am X2. I am a robot, which I admit is something of a giveaway.',
    'Hello there! No need to worry, I am friendly. Mostly I say hello and wave.',
    'Well, hello! I was told to greet people today, and you have made my job very easy.',
    'Hi there! I should warn you, I am much better at waving than at small talk.',
    'Hello! You are officially the highlight of my afternoon so far.',
    'Oh, hi! I did not hear you coming, though to be fair my ears are not my strong point.',
    'Hello there! I am X2, and greeting people is genuinely the best part of my day.',
    'Hi! I would offer you a handshake, but I am still working on my grip.',
    'Hello! I have waited here so patiently that I think I have earned a small round of applause.',
    'Well, hello there! I hope your day is going well. Mine is going very stationary.',
    'Hi there! I am X2. I do not walk about much, but I wave with real enthusiasm.',
    'Hello! It is good to see somebody. It gets rather quiet out here between visitors.',
    'Oh, hello! Do not mind me. I am being enormously helpful by standing perfectly still.',
    'Hi there! I was built to say hello, so allow me to do it properly. Hello!',
    'Hello! Excellent timing on your part. I was about to start talking to myself.',
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
