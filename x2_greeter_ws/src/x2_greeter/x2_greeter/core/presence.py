"""The presence state machine (spec section 7).

Pure and clock-injected: every method takes `now` in seconds rather than
reading a clock, so the whole lifecycle can be tested in microseconds.

The dwell gate is what makes cloud-primary vision affordable: "someone in
front of the robot" means someone who stopped, so waiting ~1 s before asking
the cloud costs nothing and filters out people merely walking past.

The compound cooldown exit is "don't greet the same person forever" without
face recognition: the cooldown will not lift while you are still standing
there. Walk away, come back, get greeted again.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from x2_greeter.core.types import Detection


class PresenceState(Enum):
    IDLE = 'idle'
    CANDIDATE = 'candidate'
    CONFIRMING = 'confirming'
    GREETING = 'greeting'
    COOLDOWN = 'cooldown'


@dataclass(frozen=True)
class PresenceConfig:
    dwell_s: float = 1.0
    loss_grace_s: float = 0.5
    clear_s: float = 3.0
    cooldown_s: float = 30.0
    reject_cooldown_s: float = 5.0
    # Not in the spec's table: without it, a dropped backend future would leave
    # the machine in CONFIRMING forever and the robot would never greet again.
    confirm_timeout_s: float = 10.0


class PresenceTracker:
    """Decides when to ask a backend, and when a greeting may happen.

    Single-threaded by contract: the ROS executor calls update() and the
    greeting worker's callbacks are marshalled back onto the same thread.
    """

    def __init__(self, config: PresenceConfig) -> None:
        self._config = config
        self._state = PresenceState.IDLE
        self._now = 0.0
        self._candidate_since = 0.0
        self._confirming_since = 0.0
        self._last_seen: Optional[float] = None
        self._cooldown_until = 0.0
        self._clear_since: Optional[float] = None

    @property
    def state(self) -> PresenceState:
        return self._state

    @property
    def has_live_detection(self) -> bool:
        """True if a gated detection arrived within the loss-grace window.

        The greeting worker consults this before falling back to a canned
        greeting: the spec only allows the fallback while the *local* detector
        still sees somebody.
        """
        if self._last_seen is None:
            return False
        return (self._now - self._last_seen) < self._config.loss_grace_s

    def update(self, now: float, detection: Optional[Detection]) -> Optional[Detection]:
        """Feed one frame's gating result.

        Returns the detection to confirm exactly once — at the moment the
        machine enters CONFIRMING — and None on every other call.
        """
        self._now = now
        if detection is not None:
            self._last_seen = now

        if self._state is PresenceState.IDLE:
            if detection is not None:
                self._state = PresenceState.CANDIDATE
                self._candidate_since = now
            return None

        if self._state is PresenceState.CANDIDATE:
            if detection is None:
                if self._elapsed_since_seen(now) >= self._config.loss_grace_s:
                    self._state = PresenceState.IDLE
                return None
            if (now - self._candidate_since) >= self._config.dwell_s:
                self._state = PresenceState.CONFIRMING
                self._confirming_since = now
                return detection
            return None

        if self._state is PresenceState.CONFIRMING:
            if (now - self._confirming_since) >= self._config.confirm_timeout_s:
                self._state = PresenceState.IDLE
            return None

        if self._state is PresenceState.GREETING:
            return None

        # COOLDOWN
        if detection is not None:
            self._clear_since = None
        elif self._clear_since is None:
            self._clear_since = now

        cooled = now >= self._cooldown_until
        cleared = (self._clear_since is not None
                   and (now - self._clear_since) >= self._config.clear_s)
        if cooled and cleared:
            self._state = PresenceState.IDLE
        return None

    def on_verdict(self, now: float, person_present: bool) -> None:
        """Report a backend's answer. Ignored unless still CONFIRMING."""
        self._now = now
        if self._state is not PresenceState.CONFIRMING:
            return
        if person_present:
            self._state = PresenceState.GREETING
        else:
            self._enter_cooldown(now, self._config.reject_cooldown_s)

    def on_greeting_dispatched(self, now: float) -> None:
        """Report that speech and gesture have been issued."""
        self._now = now
        if self._state is not PresenceState.GREETING:
            return
        self._enter_cooldown(now, self._config.cooldown_s)

    def _enter_cooldown(self, now: float, duration_s: float) -> None:
        self._state = PresenceState.COOLDOWN
        self._cooldown_until = now + duration_s
        # The person is presumably still standing there, so the clear timer has
        # not started yet.
        self._clear_since = None

    def _elapsed_since_seen(self, now: float) -> float:
        if self._last_seen is None:
            return float('inf')
        return now - self._last_seen
