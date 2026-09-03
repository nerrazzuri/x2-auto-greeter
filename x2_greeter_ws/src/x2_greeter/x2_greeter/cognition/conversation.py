"""One conversation, from the opening line to the cooldown.

Everything about when the robot talks and when it stops lives here: the
turn cap, the silence timeout, the session cap, the failure budget, the
cooldown. Nothing here imports ROS, reads a clock, or performs I/O, which
is why a three-minute session timeout can be tested in microseconds.

Time arrives as a parameter on every method that cares about it. A module
that calls time.time() itself cannot have its timeouts tested without
waiting for them, and so in practice never has them tested at all.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Tuple

from x2_greeter.cognition.dialogue import Exchange, Turn
from x2_greeter.core.language import DEFAULT_LANGUAGE, LanguagePolicy
from x2_greeter.core.scene import AddressingMode, SceneSnapshot
from x2_greeter.core.venue import VenueProfile


class SessionState(str, enum.Enum):
    IDLE = 'idle'
    GREETING = 'greeting'        # the opening line is being spoken
    LISTENING = 'listening'      # waiting for the person to say something
    THINKING = 'thinking'        # the backend has the turn
    SPEAKING = 'speaking'        # the reply is being spoken
    CLOSING = 'closing'
    COOLDOWN = 'cooldown'        # closed; will not re-greet yet


class CloseReason(str, enum.Enum):
    MODEL_ENDED = 'model_ended'
    MAX_TURNS = 'max_turns'
    SILENCE = 'silence'
    SESSION_TIMEOUT = 'session_timeout'
    BACKEND_FAILED = 'backend_failed'
    PERSON_GONE = 'person_gone'
    ABORTED = 'aborted'


@dataclass(frozen=True)
class ConversationLimits:
    max_turns: int = 8
    silence_timeout_s: float = 8.0
    session_max_s: float = 180.0
    cooldown_s: float = 20.0
    backend_failures_max: int = 2
    history_max: int = 12


# States in which nothing is being spoken and nobody is being waited on, so
# a close can take effect immediately.
_INTERRUPTIBLE = (SessionState.GREETING, SessionState.LISTENING,
                  SessionState.THINKING)


class Conversation:
    def __init__(self, venue: VenueProfile,
                 limits: ConversationLimits = ConversationLimits(),
                 language_policy: LanguagePolicy = LanguagePolicy()) -> None:
        self._venue = venue
        self._limits = limits
        self._policy = language_policy
        self.reset()

    # -- lifecycle --------------------------------------------------------

    def reset(self) -> None:
        self._state = SessionState.IDLE
        self._language = DEFAULT_LANGUAGE
        self._mode: Optional[AddressingMode] = None
        self._subject = None
        self._history: list = []
        self._turns = 0
        self._failures = 0
        self._started_at = 0.0
        self._last_event_at = 0.0
        self._closed_at = 0.0
        self._close_reason: Optional[CloseReason] = None
        self._pending_close: Optional[CloseReason] = None

    def start(self, scene: SceneSnapshot, at_s: float) -> str:
        if self._state is not SessionState.IDLE:
            raise RuntimeError(f'cannot start from {self._state.value}')
        # Addressing is decided once, here, and never revisited.
        self._mode = scene.mode
        self._subject = scene.subject
        self._language = self._venue.language_default or self._policy.default
        self._started_at = float(at_s)
        self._last_event_at = float(at_s)
        self._state = SessionState.GREETING
        return self._venue.opening_for(self._language)

    def greeted(self, at_s: float) -> None:
        """The opening line has finished playing."""
        if self._state is not SessionState.GREETING:
            return
        self._last_event_at = float(at_s)
        self._to_listening_or_close(at_s)

    def heard(self, utterance, at_s: float) -> None:
        if self._state is not SessionState.LISTENING:
            return                      # a late transcription; not a turn
        text = (getattr(utterance, 'text', '') or '').strip()
        if not text:
            self._last_event_at = float(at_s)
            return
        self._language = self._policy.next_language(
            self._language, getattr(utterance, 'language', None),
            getattr(utterance, 'confidence', 0.0))
        self._append('person', text)
        self._last_event_at = float(at_s)
        self._state = SessionState.THINKING

    def replied(self, turn: Turn, at_s: float) -> None:
        if self._state is not SessionState.THINKING:
            return
        self._language = turn.language or self._language
        self._append('robot', turn.reply)
        self._turns += 1
        self._failures = 0              # the backend answered; budget resets
        self._last_event_at = float(at_s)
        self._state = SessionState.SPEAKING
        if turn.end:
            self._pending_close = CloseReason.MODEL_ENDED
        elif self._turns >= self._limits.max_turns:
            self._pending_close = CloseReason.MAX_TURNS

    def finished_speaking(self, at_s: float) -> None:
        if self._state is not SessionState.SPEAKING:
            return
        self._last_event_at = float(at_s)
        self._to_listening_or_close(at_s)

    def backend_failed(self, at_s: float) -> bool:
        """Returns True when this failure ended the session."""
        if self._state is not SessionState.THINKING:
            return False
        self._failures += 1
        self._last_event_at = float(at_s)
        if self._failures >= self._limits.backend_failures_max:
            self.close(CloseReason.BACKEND_FAILED, at_s)
            return True
        self._state = SessionState.LISTENING
        return False

    def observed(self, scene: SceneSnapshot, at_s: float) -> None:
        """A fresh look at the room. Only presence matters -- the addressing
        decision was made at start() and stays made."""
        if not self.active:
            return
        if scene.person_count == 0:
            self.close(CloseReason.PERSON_GONE, at_s)

    def tick(self, at_s: float) -> None:
        if not self.active:
            return
        if float(at_s) - self._started_at > self._limits.session_max_s:
            self.close(CloseReason.SESSION_TIMEOUT, at_s)
            return
        if (self._state is SessionState.LISTENING
                and float(at_s) - self._last_event_at
                > self._limits.silence_timeout_s):
            self.close(CloseReason.SILENCE, at_s)

    def close(self, reason: CloseReason, at_s: float) -> None:
        if self._close_reason is not None:
            return                      # the first reason wins
        if self._state in (SessionState.SPEAKING,):
            # Never cut a sentence in half. The close lands when the words
            # have finished.
            self._pending_close = self._pending_close or reason
            return
        self._commit_close(reason, at_s)

    def cooldown_active(self, at_s: float) -> bool:
        if self._close_reason is None:
            return False
        return float(at_s) - self._closed_at < self._limits.cooldown_s

    # -- properties -------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state in _INTERRUPTIBLE + (SessionState.SPEAKING,)

    @property
    def language(self) -> str:
        return self._language

    @property
    def turns_taken(self) -> int:
        return self._turns

    @property
    def mode(self) -> Optional[AddressingMode]:
        return self._mode

    @property
    def subject(self):
        return self._subject

    @property
    def history(self) -> Tuple[Exchange, ...]:
        return tuple(self._history)

    @property
    def close_reason(self) -> Optional[CloseReason]:
        return self._close_reason

    # -- internals --------------------------------------------------------

    def _append(self, speaker: str, text: str) -> None:
        self._history.append(Exchange(speaker, text))
        excess = len(self._history) - self._limits.history_max
        if excess > 0:
            del self._history[:excess]

    def _to_listening_or_close(self, at_s: float) -> None:
        if self._pending_close is not None:
            self._commit_close(self._pending_close, at_s)
        else:
            self._state = SessionState.LISTENING

    def _commit_close(self, reason: CloseReason, at_s: float) -> None:
        self._close_reason = reason
        self._closed_at = float(at_s)
        self._state = SessionState.COOLDOWN
