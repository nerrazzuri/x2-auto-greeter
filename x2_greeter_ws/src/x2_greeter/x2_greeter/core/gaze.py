"""Where to point the head, expressed as arithmetic and nothing else.

The vendor exposes head yaw over /aima/hal/joint/head/command and documents a
+/-20 degree range; pitch is listed but unavailable. We clamp to +/-15 so the
margin absorbs a bad FOV constant, a malformed bounding box or a rounding
error before the joint reaches its hard stop.

Two behaviours live here. yaw_for() points the head at one person. The sweep
turns the head slowly left, holds, right, holds, and returns to centre. The
sweep is NOT a way to see more of the room: 94 degrees of head FOV plus 40
degrees of travel is 134 degrees, less than the 156 the fixed stereo camera
already covers in a single frame. It exists so a person can watch the robot
take the room in, and so the head camera can capture two off-axis close-range
frames that the wide lens renders too small to read.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Tuple

MAX_YAW_RAD = 0.262        # 15 degrees, our clamp
VENDOR_LIMIT_RAD = 0.349   # 20 degrees, the vendor's documented range
HEAD_HFOV_RAD = 1.6406     # 94 degrees, head RGB-D colour horizontal FOV

HEAD_YAW_JOINT = 'head_yaw'
HEAD_PITCH_JOINT = 'head_pitch'


def clamp_yaw(yaw: float, max_yaw_rad: float = MAX_YAW_RAD) -> float:
    """Bound a yaw command, treating a non-finite value as "no idea": centre."""
    value = float(yaw)
    if not math.isfinite(value):
        return 0.0
    limit = abs(float(max_yaw_rad))
    return max(-limit, min(limit, value))


def yaw_for(bbox_cx: float, image_width: int,
            hfov_rad: float = HEAD_HFOV_RAD,
            max_yaw_rad: float = MAX_YAW_RAD,
            yaw_sign: int = 1) -> float:
    """Yaw that brings a bounding box centred at bbox_cx to the middle of frame.

    yaw_sign exists because nobody has measured whether a positive command
    turns the head left or right (spec section 18). It is a configuration
    value with a default, not an assumption compiled into the geometry.
    """
    width = int(image_width)
    if width <= 0:
        return 0.0
    half = width / 2.0
    offset = (float(bbox_cx) - half) / half    # -1 at left edge, +1 at right
    if not math.isfinite(offset):
        return 0.0
    return clamp_yaw(int(yaw_sign) * offset * (float(hfov_rad) / 2.0), max_yaw_rad)


class SweepStep(NamedTuple):
    """One 20 Hz command in the sweep. capture is True only at a hold point."""

    yaw: float
    t_s: float
    capture: bool


def sweep_waypoints(leg_s: float = 1.0, hold_s: float = 0.4,
                    rate_hz: float = 20.0,
                    max_yaw_rad: float = MAX_YAW_RAD) -> Tuple[SweepStep, ...]:
    """centre -> left -> hold -> right -> hold -> centre, sampled at rate_hz.

    Returned as a full command trajectory rather than four target angles: the
    joint interface takes positions, so somebody has to do the interpolation,
    and doing it here means the +/-15 clamp is proved over every intermediate
    sample, not only at the corners.
    """
    rate_hz = float(rate_hz)
    if rate_hz <= 0.0:
        raise ValueError('rate_hz must be positive')
    dt = 1.0 / rate_hz
    limit = abs(float(max_yaw_rad))

    steps = [SweepStep(yaw=0.0, t_s=0.0, capture=False)]

    def _ramp(start: float, end: float, seconds: float) -> None:
        count = max(1, int(round(float(seconds) * rate_hz)))
        for i in range(1, count + 1):
            yaw = start + (end - start) * (i / count)
            steps.append(SweepStep(yaw=clamp_yaw(yaw, limit),
                                   t_s=steps[-1].t_s + dt, capture=False))

    def _hold(yaw: float, seconds: float) -> None:
        count = max(1, int(round(float(seconds) * rate_hz)))
        for i in range(1, count + 1):
            # Capture on the last sample of the hold: by then the head has had
            # the whole hold to settle, so the frame is not motion-blurred.
            steps.append(SweepStep(yaw=clamp_yaw(yaw, limit),
                                   t_s=steps[-1].t_s + dt,
                                   capture=(i == count)))

    _ramp(0.0, -limit, leg_s)
    _hold(-limit, hold_s)
    _ramp(-limit, limit, 2.0 * leg_s)
    _hold(limit, hold_s)
    _ramp(limit, 0.0, leg_s)
    return tuple(steps)


def group_drift(t_s: float, period_s: float = 12.0,
                max_yaw_rad: float = MAX_YAW_RAD) -> float:
    """A slow sinusoidal wander for GROUP addressing, at 60% of the clamp.

    Locking onto one face while addressing a group reads as staring; holding
    dead centre reads as a screensaver. This is neither, and it is bounded by
    the same clamp as everything else.
    """
    period_s = float(period_s)
    if period_s <= 0.0:
        return 0.0
    amplitude = 0.6 * abs(float(max_yaw_rad))
    return clamp_yaw(amplitude * math.sin(2.0 * math.pi * float(t_s) / period_s),
                     max_yaw_rad)
