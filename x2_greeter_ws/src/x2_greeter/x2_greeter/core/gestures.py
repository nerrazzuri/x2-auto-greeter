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

#: McControlArea is a bit mask: LEFT_HAND 1, RIGHT_HAND 2, HEAD 4, WAIST 8.
AREA_LEFT = 1
AREA_RIGHT = 2
AREA_BOTH = 3
AREA_WAIST = 8
AREA_WHOLE_BODY = 11          # left + right + waist


def uses_waist(spec) -> bool:
    """Does any of this gesture's areas command the waist?

    The waist bit is what separates an arm wave from a bow: a motion that
    moves the waist moves the robot's centre of mass. That distinction comes
    from the vendor's own area mask, not from a judgement about which motions
    look gentle, which is why it is safe to test against.
    """
    return any(area & AREA_WAIST for area in spec.areas)

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
    # Every motion_id here is a member of the vendor's McPresetMotion enum.
    # test_catalogue_ids_are_real_vendor_motions pins that against the SDK's
    # own message, because three of these were once invented from a
    # description and the controller silently refused them.
    # --- enabled by default: greetings ---
    'wave': _spec('wave', 1002, (AREA_RIGHT, AREA_LEFT), True),
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
    'heart': _spec('heart', 3004, (AREA_WHOLE_BODY,), False),
    # --- available but disabled by default (spec section 9) ---
    'hug': _spec('hug', 3008, (AREA_WHOLE_BODY,), False),
    'clap': _spec('clap', 3015, (AREA_WHOLE_BODY,), False),
    'cross_arms': _spec('cross_arms', 3009, (AREA_WHOLE_BODY,), False),
    'dynamic_light_wave': _spec('dynamic_light_wave', 3007, (AREA_WHOLE_BODY,), False),
    'like': _spec('like', 3002, (AREA_WHOLE_BODY,), False),
    'peace': _spec('peace', 3003, (AREA_WHOLE_BODY,), False),
    'fist_bump': _spec('fist_bump', 1009, (AREA_RIGHT, AREA_LEFT), True),
    'turn_wave': _spec('turn_wave', 2001, (AREA_WHOLE_BODY,), False),
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

    def select(self, requested: Optional[str] = None,
               allowed: Optional[Sequence[str]] = None) -> GestureChoice:
        """Honour `requested` if it is on the allowlist, otherwise choose randomly.

        `allowed` narrows the pool further -- the walking whitelist, say. It
        never widens it: a name outside `enabled` is not reachable through it,
        so a stale walking list cannot re-enable a gesture the operator has
        switched off.
        """
        pool = self._enabled
        if allowed is not None:
            pool = tuple(n for n in self._enabled if n in set(allowed))
            if not pool:
                raise ValueError('no gesture is both enabled and allowed here')
        # A requested gesture is honoured unless it repeats the last one.
        #
        # Two reasons, and the second is not cosmetic. The cloud picks the
        # gesture from the scene, and for "a stranger has walked up" the
        # sensible pick is a wave every single time -- 34 greetings in a row on
        # hardware, all waves, while ten other gestures sat enabled and unused.
        # And the controller refuses the same preset motion twice in a row, so
        # a repeat is not merely dull: roughly every other one is rejected and
        # the robot makes no movement at all.
        #
        # Breaking only the repeat keeps the cloud's judgement everywhere it
        # actually differs -- a handshake for an offered hand, a wave goodbye
        # for somebody leaving.
        if requested in pool and requested != self._last:
            name = requested
        else:
            name = self._random_name(pool)
        self._last = name
        spec = CATALOGUE[name]
        return GestureChoice(name=name, motion_id=spec.motion_id,
                             area_id=resolve_area(spec, self._hand_preference, self._rng))

    def _random_name(self, pool: Optional[Sequence[str]] = None) -> str:
        pool = tuple(self._enabled if pool is None else pool)
        # Avoid repeating the previous choice -- the controller refuses the
        # same preset motion twice in a row. With a one-entry pool there is no
        # choice to make, and the refusal is unavoidable.
        candidates = [name for name in pool if name != self._last]
        if not candidates:
            candidates = list(pool)
        return self._rng.choice(candidates)
