"""The face-screen emoji catalogue and the policy for choosing one.

Emotion ids are constants on aimdk_msgs/srv/PlayEmoji; test_face_catalogue_ids
pins every one of them against that message. An LLM never supplies an id -- it
supplies a *name*, validated against the enabled allowlist before it becomes
one.

Unlike a gesture, an emoji moves nothing and can hurt nobody, so the
catalogue ships enabled and the hw_verified flag means only "somebody has
watched this appear on the real face screen" -- which, today, nobody has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

MODE_ONCE = 1   # PlayEmoji.EMOTION_MODE_ONCE
MODE_LOOP = 2   # PlayEmoji.EMOTION_MODE_LOOP

THINKING = 'thinking'


@dataclass(frozen=True)
class EmojiSpec:
    """One face expression. source is 'srv' -- the PlayEmoji service constants."""

    name: str
    emotion_id: int
    source: str = 'srv'
    hw_verified: bool = False


def _spec(name: str, emotion_id: int) -> EmojiSpec:
    return EmojiSpec(name=name, emotion_id=emotion_id)


CATALOGUE: Dict[str, EmojiSpec] = {
    # --- the latency cover ---
    'thinking': _spec('thinking', 170),      # EMOTION_EYE_THINKING
    # --- everyday conversational faces ---
    'calm': _spec('calm', 10),               # EMOTION_IDLE_CALM_1
    'blink': _spec('blink', 1),              # EMOTION_IDLE_BLINK
    'happy': _spec('happy', 90),             # EMOTION_EYE_HAPPY
    'very_happy': _spec('very_happy', 100),  # EMOTION_EYE_EXTREMEHAPPY_1
    'cute': _spec('cute', 30),               # EMOTION_IDLE_CUTE_1
    'adore': _spec('adore', 200),            # EMOTION_EYE_ADORE
    'confused': _spec('confused', 130),      # EMOTION_EYE_CONFUSE
    # --- catalogued but not enabled by default ---
    'shock': _spec('shock', 140),            # EMOTION_EYE_SHOCK
    'sad': _spec('sad', 110),                # EMOTION_EYE_SAD
    'sympathy': _spec('sympathy', 120),      # EMOTION_EYE_SYMPATHY
    'serious': _spec('serious', 160),        # EMOTION_EYE_SERIOUS
    # The vendor also offers EMOTION_EYE_ANGRY (180) and EXTREMEANGRY (190).
    # They are deliberately absent: a greeter has no use for either, and a
    # name that does not exist is a name the model cannot choose.
}

DEFAULT_ENABLED: Tuple[str, ...] = (
    'thinking', 'calm', 'blink', 'happy', 'very_happy', 'cute', 'adore',
    'confused',
)


class EmojiSelector:
    """Turns an optional emoji *name* into a vendor spec, or into nothing.

    Unlike GestureSelector this never falls back to a random choice. A gesture
    is part of a greeting and something is better than nothing; an expression
    nobody asked for is just a robot pulling a face mid-sentence.
    """

    def __init__(self, enabled: Sequence[str]) -> None:
        enabled = tuple(enabled)
        unknown = [name for name in enabled if name not in CATALOGUE]
        if unknown:
            raise ValueError(
                f'unknown emoji in face.enabled: {", ".join(unknown)}')
        self._enabled = enabled

    @property
    def enabled_names(self) -> Tuple[str, ...]:
        return self._enabled

    @property
    def thinking(self) -> Optional[EmojiSpec]:
        return CATALOGUE[THINKING] if THINKING in self._enabled else None

    def select(self, requested: Optional[str]) -> Optional[EmojiSpec]:
        if requested in self._enabled:
            return CATALOGUE[requested]
        return None
