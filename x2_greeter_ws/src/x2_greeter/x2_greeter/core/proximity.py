"""Is anybody within arm's reach?

Kept apart from the presence gate on purpose. Choosing *whom to greet* wants
one central person between distance_min_m and distance_max_m; deciding
*whether an arm may move* wants everyone in view, wherever they stand. Using
the greeting target's distance for both let a person gated further away mask
somebody already standing inside the floor.

Pure and clock-injected, like core.presence. Not thread-safe: GreetingNode
holds its tracker lock around every call.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Clearance:
    clear: bool
    reason: str


class ProximityMonitor:
    """Remembers the last `window_s` of frames and answers from all of them.

    One frame is not enough: a detector that misses somebody close for a
    single frame must not open the interlock. Every frame in the window has
    to agree that nobody is inside `floor_m`.
    """

    def __init__(self, floor_m: float, window_s: float) -> None:
        self._floor_m = float(floor_m)
        self._window_s = float(window_s)
        # (time, nearest measured distance or None if nobody measured,
        #  whether anybody's distance could not be read)
        self._frames: Deque[Tuple[float, Optional[float], bool]] = deque()

    def observe(self, now: float, distances: Sequence[Optional[float]]) -> None:
        """Record one RGB-D frame: every person's distance, None if unreadable."""
        measured = [d for d in distances if d is not None]
        nearest = min(measured) if measured else None
        self._frames.append((now, nearest, len(measured) != len(distances)))
        self._forget_before(now)

    def clearance(self, now: float) -> Clearance:
        self._forget_before(now)
        if not self._frames:
            return Clearance(False, f'no depth frame in the last {self._window_s:.2f}s')
        if any(unreadable for _, _, unreadable in self._frames):
            return Clearance(False, "a person's distance could not be read")
        nearest = min((n for _, n, _ in self._frames if n is not None), default=None)
        if nearest is not None and nearest < self._floor_m:
            return Clearance(False, f'a person is {nearest:.2f} m away, inside '
                                    f'{self._floor_m:.2f} m')
        return Clearance(True, '')

    def _forget_before(self, now: float) -> None:
        while self._frames and (now - self._frames[0][0]) > self._window_s:
            self._frames.popleft()
