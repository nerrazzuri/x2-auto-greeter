"""One turn of conversation: the contract, and the validation that enforces it.

A dialogue backend returns five fields. Four of them are advisory -- a
gesture name, an emoji name, a language tag, a stop flag -- and one, the
reply, is the whole point. So a malformed advisory field is dropped and the
turn continues, while a missing reply raises: a robot that waves at somebody
in silence is worse than one that does nothing.

This lives beside the port rather than inside the Claude adapter because
every backend gets the same treatment. Models invent gesture names, answer
'English' when asked for 'en', and write paragraphs when asked for a
sentence. That is not a property of one vendor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple, Optional, Protocol, Sequence

from x2_greeter.cognition.port import BackendUnavailable   # re-exported
from x2_greeter.core.language import ALLOWED_LANGUAGES, normalise_language

__all__ = ['BackendUnavailable', 'DialogueBackend', 'Exchange', 'Turn',
           'TurnLimits', 'TurnRejected', 'build_turn_schema',
           'truncate_at_sentence', 'validate_turn']

# Both scripts' sentence terminators. Chinese full stops are not '.' and a
# reply that ends on one must not be cut mid-clause for want of an ASCII dot.
_SENTENCE_END = re.compile(r'[.!?。！？]')


class TurnRejected(Exception):
    """The backend returned something that is not a usable turn."""


@dataclass(frozen=True)
class Turn:
    reply: str
    language: str
    gesture: Optional[str]
    emoji: Optional[str]
    end: bool


class Exchange(NamedTuple):
    speaker: str        # 'person' or 'robot'
    text: str


@dataclass(frozen=True)
class TurnLimits:
    reply_max_chars: int = 200


class DialogueBackend(Protocol):
    def respond(self, base_frame, frame, scene, venue,
                history: Sequence[Exchange], utterance, language: str,
                child: bool) -> Turn:
        ...


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """Cut to at most max_chars, preferring the last sentence that fits.

    Falling back to a word boundary, and only then to a hard cut. Mid-word
    truncation sounds like the robot was interrupted; a clean sentence just
    sounds brief.
    """
    text = str(text).strip()
    limit = int(max_chars)
    if limit <= 0 or len(text) <= limit:
        return text

    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends:
        return window[:ends[-1]].strip()
    space = window.rfind(' ')
    if space > 0:
        return window[:space].strip()
    return window.strip()


def validate_turn(doc: Any, enabled_gestures: Sequence[str],
                  enabled_emoji: Sequence[str], limits: TurnLimits,
                  fallback_language: str) -> Turn:
    if not isinstance(doc, Mapping):
        raise TurnRejected(f'expected a JSON object, got {type(doc).__name__}')

    raw_reply = doc.get('reply')
    if not isinstance(raw_reply, str) or not raw_reply.strip():
        raise TurnRejected('turn has no usable reply text')

    language = (normalise_language(doc.get('language'))
                or normalise_language(fallback_language)
                or ALLOWED_LANGUAGES[0])

    gesture = doc.get('gesture')
    if gesture not in tuple(enabled_gestures):
        gesture = None
    emoji = doc.get('emoji')
    if emoji not in tuple(enabled_emoji):
        emoji = None

    end = doc.get('end')
    return Turn(
        reply=truncate_at_sentence(raw_reply, limits.reply_max_chars),
        language=language,
        gesture=gesture,
        emoji=emoji,
        end=end if isinstance(end, bool) else False,
    )


def build_turn_schema(enabled_gestures: Sequence[str],
                      enabled_emoji: Sequence[str]) -> dict:
    """The JSON schema handed to the model.

    The allowlists appear as enums so the model is steered towards a valid
    name, but validate_turn still checks -- a schema is a request, and the
    only thing that has ever stopped an invented gesture reaching the
    controller is the check on our side.
    """
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['reply', 'language', 'gesture', 'emoji', 'end'],
        'properties': {
            'reply': {'type': 'string'},
            'language': {'type': 'string', 'enum': list(ALLOWED_LANGUAGES)},
            'gesture': {'enum': list(enabled_gestures) + [None]},
            'emoji': {'enum': list(enabled_emoji) + [None]},
            'end': {'type': 'boolean'},
        },
    }
