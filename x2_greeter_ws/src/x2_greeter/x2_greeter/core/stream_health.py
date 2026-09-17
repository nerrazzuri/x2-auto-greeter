"""Is the camera actually giving us anything to gate on?

Judged on usable RGB-D *pairs*, not on colour frames. Depth is mandatory, so
a colour stream at full rate with a dead depth stream gates nothing -- and a
watchdog fed by colour alone reports that as healthy for as long as it lasts.
The warning names both streams, because "which one stopped" is the first
question anybody on site will ask.

Pure and clock-injected. Timing starts at construction, so a camera that
never sends a single frame is reported too.
"""
from __future__ import annotations

from typing import Optional


class StreamHealth:
    def __init__(self, stale_s: float, started_at: float) -> None:
        self._stale_s = float(stale_s)
        self._started_at = float(started_at)
        self._last_rgb: Optional[float] = None
        self._last_depth: Optional[float] = None
        self._last_pair: Optional[float] = None
        self._pairs = 0
        self._warned = False

    @property
    def pairs(self) -> int:
        return self._pairs

    def rgb(self, now: float) -> None:
        self._last_rgb = now

    def depth(self, now: float) -> None:
        self._last_depth = now

    def pair(self, now: float) -> bool:
        """Record a usable pair. True if it ends an outage that was warned about."""
        self._last_pair = now
        self._pairs += 1
        recovered = self._warned
        self._warned = False
        return recovered

    def check(self, now: float) -> Optional[str]:
        """A warning the first time pairs have stopped for stale_s, else None."""
        since = self._last_pair if self._last_pair is not None else self._started_at
        if self._warned or (now - since) <= self._stale_s:
            return None
        self._warned = True
        message = (f'no usable RGB-D frame for {now - since:.0f}s, so nobody is being '
                   f'gated: {self._describe("colour", self._last_rgb, now)}, '
                   f'{self._describe("depth", self._last_depth, now)}')
        if self._arriving(self._last_rgb, now) and self._arriving(self._last_depth, now):
            message += ('; both streams are arriving but not pairing (timestamp skew, '
                        'or calibration not received)')
        return message

    def _arriving(self, last: Optional[float], now: float) -> bool:
        return last is not None and (now - last) <= self._stale_s

    @staticmethod
    def _describe(name: str, last: Optional[float], now: float) -> str:
        if last is None:
            return f'{name} never arrived'
        return f'{name} last arrived {now - last:.1f}s ago'
