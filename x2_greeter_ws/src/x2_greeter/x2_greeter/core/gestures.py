"""The preset-motion catalogue and the policy for choosing one.

Motion and area IDs come from the AimDK interface docs
(dev/Interface/control_mod/preset_motion.html). An LLM never supplies a motion
ID — it supplies a *name*, which is validated against the enabled allowlist
before it is ever turned into an ID.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import NamedTuple, Optional, Sequence

AREA_LEFT = 1
AREA_RIGHT = 2
AREA_BOTH = 3
AREA_WHOLE_BODY = 11

_HAND_PREFERENCE_AREAS = {
    'left': AREA_LEFT,
    'right': AREA_RIGHT,
    'both': AREA_BOTH,
}
VALID_HAND_PREFERENCES = frozenset(_HAND_PREFERENCE_AREAS) | {'random'}


@dataclass(frozen=True)
class GestureSpec:
    """One preset motion.

    areas lists the control areas the docs permit, most-preferred first — so
    areas[0] is the fallback when a hand preference cannot be honoured.
    handed is True when the gesture has left/right variants and should follow
    gestures.hand_preference.
    """

    name: str
    motion_id: int
    areas: tuple
    handed: bool


def _spec(name: str, motion_id: int, areas: tuple, handed: bool) -> GestureSpec:
    return GestureSpec(name=name, motion_id=motion_id, areas=areas, handed=handed)


CATALOGUE = {
    # --- enabled by default: greetings ---
    'wave': _spec('wave', 1002, (AREA_RIGHT, AREA_LEFT), True),
    'salute': _spec('salute', 1013, (AREA_RIGHT, AREA_LEFT), True),
    'handshake': _spec('handshake', 1003, (AREA_RIGHT, AREA_LEFT), True),
    'raise_hand': _spec('raise_hand', 1001, (AREA_RIGHT, AREA_LEFT), True),
    'raise_both': _spec('raise_both', 1010, (AREA_BOTH,), False),
    'bow': _spec('bow', 3001, (AREA_WHOLE_BODY,), False),
    'high_five': _spec('high_five', 1008, (AREA_RIGHT, AREA_LEFT), True),
    'wave_chest': _spec('wave_chest', 1011, (AREA_RIGHT, AREA_LEFT), True),
    'cheer': _spec('cheer', 3011, (AREA_WHOLE_BODY,), False),
    'blow_kiss': _spec('blow_kiss', 1004, (AREA_RIGHT, AREA_LEFT), True),
    'heart': _spec('heart', 1007, (AREA_BOTH, AREA_RIGHT, AREA_LEFT), True),
    # --- available but disabled by default (spec section 9) ---
    'hug': _spec('hug', 3008, (AREA_WHOLE_BODY,), False),
    'wave_goodbye': _spec('wave_goodbye', 3031, (AREA_WHOLE_BODY,), False),
    'clap': _spec('clap', 3017, (AREA_WHOLE_BODY,), False),
    'cross_arms': _spec('cross_arms', 3009, (AREA_WHOLE_BODY,), False),
    'scratch_head': _spec('scratch_head', 3024, (AREA_WHOLE_BODY,), False),
    'grab_buttocks': _spec('grab_buttocks', 3025, (AREA_WHOLE_BODY,), False),
    'dynamic_light_wave': _spec('dynamic_light_wave', 3007, (AREA_WHOLE_BODY,), False),
}

DEFAULT_ENABLED = (
    'wave', 'salute', 'handshake', 'raise_hand', 'raise_both', 'bow',
    'high_five', 'wave_chest', 'cheer', 'blow_kiss', 'heart',
)


class GestureChoice(NamedTuple):
    name: str
    motion_id: int
    area_id: int


def resolve_area(spec: GestureSpec, hand_preference: str, rng: random.Random) -> int:
    """Pick the control area for one performance of a gesture.

    Whole-body and both-arm-only gestures ignore the preference entirely; a
    preference the gesture does not offer falls back to its documented default.
    """
    if not spec.handed:
        return spec.areas[0]
    if hand_preference == 'random':
        hand_preference = rng.choice(('left', 'right'))
    wanted = _HAND_PREFERENCE_AREAS.get(hand_preference)
    if wanted in spec.areas:
        return wanted
    return spec.areas[0]


class GestureSelector:
    """Turns an optional gesture *name* into a concrete (motion, area) pair.

    Not thread-safe: it holds the previous choice so it can avoid repeating
    itself. The greeter calls it from a single worker thread.
    """

    def __init__(self, enabled: Sequence[str], hand_preference: str = 'right',
                 rng: Optional[random.Random] = None) -> None:
        enabled = tuple(enabled)
        if not enabled:
            raise ValueError('gestures.enabled must list at least one gesture')
        unknown = [name for name in enabled if name not in CATALOGUE]
        if unknown:
            raise ValueError(f'unknown gesture(s) in gestures.enabled: {", ".join(unknown)}')
        if hand_preference not in VALID_HAND_PREFERENCES:
            raise ValueError(
                f'hand_preference must be one of {sorted(VALID_HAND_PREFERENCES)}, '
                f'got {hand_preference!r}')
        self._enabled = enabled
        self._hand_preference = hand_preference
        self._rng = rng if rng is not None else random.Random()
        self._last: Optional[str] = None

    @property
    def enabled_names(self) -> tuple:
        return self._enabled

    def select(self, requested: Optional[str] = None) -> GestureChoice:
        """Honour `requested` if it is on the allowlist, otherwise choose randomly."""
        if requested in self._enabled:
            name = requested
        else:
            name = self._random_name()
        self._last = name
        spec = CATALOGUE[name]
        return GestureChoice(name=name, motion_id=spec.motion_id,
                             area_id=resolve_area(spec, self._hand_preference, self._rng))

    def _random_name(self) -> str:
        pool = [name for name in self._enabled if name != self._last]
        if not pool:
            pool = list(self._enabled)
        return self._rng.choice(pool)
