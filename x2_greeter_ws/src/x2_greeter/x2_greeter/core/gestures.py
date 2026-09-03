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

VALID_SOURCES = frozenset({'enum', 'doc_table'})


@dataclass(frozen=True)
class GestureSpec:
    """One preset motion.

    areas lists the control areas the docs permit, most-preferred first -- so
    areas[0] is the fallback when a hand preference cannot be honoured.
    handed is True when the gesture has left/right variants and should follow
    gestures.hand_preference.

    source names where the (motion_id, area) pair came from: 'enum' for the
    McPresetMotion message, 'doc_table' for the interface docs' preset-motion
    table. The vendor's two sources disagree and neither contains the other,
    so an entry that does not say which one it came from cannot be checked.

    hw_verified is True only when a human has watched a real robot perform
    this exact pair. It is not a confidence rating.
    """

    name: str
    motion_id: int
    areas: tuple
    handed: bool
    source: str
    hw_verified: bool = False


def _spec(name: str, motion_id: int, areas: tuple, handed: bool,
          source: str = 'enum', hw_verified: bool = False) -> GestureSpec:
    if source not in VALID_SOURCES:
        raise ValueError(f'gesture {name}: source must be one of '
                         f'{sorted(VALID_SOURCES)}, got {source!r}')
    return GestureSpec(name=name, motion_id=motion_id, areas=areas,
                       handed=handed, source=source, hw_verified=hw_verified)


CATALOGUE = {
    # Every motion_id here is a member of the vendor's McPresetMotion enum,
    # or -- where noted -- of the interface doc's preset-motion table. See
    # test/ros/test_gesture_catalogue_ids.py, which pins every (motion_id,
    # area) pair against the source its spec names.
    # --- enabled by default: greetings ---
    'wave': _spec('wave', 1002, (AREA_RIGHT, AREA_LEFT), True, hw_verified=True),
    'salute': _spec('salute', 1013, (AREA_RIGHT, AREA_LEFT), True),
    'handshake': _spec('handshake', 1003, (AREA_RIGHT, AREA_LEFT), True),
    'raise_hand': _spec('raise_hand', 1001, (AREA_RIGHT, AREA_LEFT), True),
    # RAISE_HAND with both hands in the area mask -- the areas are bit flags,
    # so there is no separate "raise both" motion and 1010 was never one.
    'raise_both': _spec('raise_both', 1001, (AREA_BOTH,), False),
    'bow': _spec('bow', 3001, (AREA_WHOLE_BODY,), False),
    'high_five': _spec('high_five', 1008, (AREA_RIGHT, AREA_LEFT), True),
    'wave_chest': _spec('wave_chest', 3010, (AREA_WHOLE_BODY,), False),
    'cheer': _spec('cheer', 3011, (AREA_WHOLE_BODY,), False),
    'blow_kiss': _spec('blow_kiss', 1004, (AREA_RIGHT, AREA_LEFT), True),
    # 1007 is the two-handed chest heart. It is NOT in McPresetMotion -- the
    # enum's heart is 3004, the overhead one below -- so its source is the
    # doc table, and it takes area 3 only. Phase 1 shipped it handed=True,
    # which with hand_preference: right sent a two-handed motion a one-handed
    # area; the controller refused it and the refusal was mistaken for the id
    # being invented.
    'heart': _spec('heart', 1007, (AREA_BOTH,), False, source='doc_table'),
    # --- available but disabled by default (spec section 9) ---
    'hug': _spec('hug', 3008, (AREA_WHOLE_BODY,), False),
    'clap': _spec('clap', 3015, (AREA_WHOLE_BODY,), False),
    'cross_arms': _spec('cross_arms', 3009, (AREA_WHOLE_BODY,), False),
    'dynamic_light_wave': _spec('dynamic_light_wave', 3007, (AREA_WHOLE_BODY,), False),
    'like': _spec('like', 3002, (AREA_WHOLE_BODY,), False),
    'peace': _spec('peace', 3003, (AREA_WHOLE_BODY,), False),
    'fist_bump': _spec('fist_bump', 1009, (AREA_RIGHT, AREA_LEFT), True),
    'turn_wave': _spec('turn_wave', 2001, (AREA_WHOLE_BODY,), False),
    # The enum's INTERACTION_SWEATHEART: hands above the head. Area 11 is an
    # inference from it being a whole-body motion, not a row anyone has read,
    # which is exactly why this ships disabled.
    'heart_overhead': _spec('heart_overhead', 3004, (AREA_WHOLE_BODY,), False),
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
