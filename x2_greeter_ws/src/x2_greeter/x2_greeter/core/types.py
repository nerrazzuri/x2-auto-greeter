"""Value types shared across the greeter.

Pure data. No I/O, no ROS, no OpenCV — importable anywhere, including on a
Windows workstation with no ROS installation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BBox:
    """An axis-aligned box in RGB pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass(frozen=True)
class RawDetection:
    """What a person detector reports, before any gating."""

    bbox: BBox
    confidence: float


@dataclass(frozen=True)
class Detection:
    """A detection that has passed every gate in core.detection.

    center_offset is signed and normalised: 0.0 is dead centre, -0.5 is the
    left edge of frame, +0.5 the right edge.
    """

    bbox: BBox
    confidence: float
    distance_m: float
    center_offset: float


@dataclass(frozen=True)
class SceneContext:
    """What the local detector knows about the person, passed to a backend."""

    distance_m: float
    center_offset: float


@dataclass(frozen=True)
class Verdict:
    """A backend's answer: is this a person, and what should the robot do?

    gesture is a gesture *name* from the enabled allowlist, or None to let the
    selector choose at random. It is never a motion ID.
    """

    person_present: bool
    facing_robot: bool
    confidence: float
    greeting: str
    reason: str
    source: str
    gesture: Optional[str] = None


@dataclass(frozen=True)
class JpegFrame:
    """An encoded frame on its way to a cloud backend. Never written to disk."""

    data: bytes
    media_type: str = 'image/jpeg'
