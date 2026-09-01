"""Cloud first, canned second — but only on failure.

The distinction that matters: a backend *failing* means fall back; a backend
*disagreeing* ("that is a poster, not a person") is an answer and is respected.
Overriding a negative verdict would make the cloud confirmation pointless.
"""
from __future__ import annotations

import logging
from typing import Optional

from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict


class GreetingPolicy:
    def __init__(self, primary: GreetingBackend, fallback: GreetingBackend,
                 logger: Optional[logging.Logger] = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._log = logger or logging.getLogger(__name__)
        self._fallback_count = 0

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    def compose(self, frame: Optional[JpegFrame], ctx: SceneContext) -> Verdict:
        """Ask the primary backend; on any failure, use the fallback.

        Runs on the greeting worker thread. The 2.5 s budget is enforced by the
        primary backend's own client timeout, so this never blocks the ROS
        executor.
        """
        try:
            return self._primary.confirm_and_compose(frame, ctx)
        except BackendUnavailable as exc:
            self._log.warning('%s backend unavailable (%s); using %s',
                              self._primary.name, exc, self._fallback.name)
        except Exception as exc:                       # noqa: BLE001 - never reach the node
            self._log.warning('%s backend raised %s; using %s',
                              self._primary.name, type(exc).__name__, self._fallback.name)

        self._fallback_count += 1
        # The fallback is never shown the frame: it does not look at images, and
        # not passing it keeps the buffer's lifetime as short as possible.
        return self._fallback.confirm_and_compose(None, ctx)
