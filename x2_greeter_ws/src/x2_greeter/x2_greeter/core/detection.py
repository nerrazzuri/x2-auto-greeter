"""Turning raw detector boxes into a single gated Detection, or nothing.

Three gates, all of which must pass (spec section 6): confidence, distance
from the depth frame, and centring. Depth is never guessed — an absent or
unreadable depth reading rejects the detection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, runtime_checkable

import numpy as np

from x2_greeter.core.types import BBox, Detection, RawDetection


@runtime_checkable
class PersonDetector(Protocol):
    """Anything that can find people in a BGR frame."""

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        ...


@dataclass(frozen=True)
class GateConfig:
    confidence_min: float = 0.5
    distance_min_m: float = 1.0
    distance_max_m: float = 3.0
    center_tolerance: float = 0.25


def median_depth_m(depth: Optional[np.ndarray], bbox: BBox, rgb_shape: tuple,
                   depth_scale: float) -> Optional[float]:
    """Median distance in metres over the bbox, or None if it cannot be read.

    The depth image may be a different resolution from the RGB image, so the
    box is rescaled by the per-axis ratio between the two. depth_scale is
    metres per depth unit: 0.001 for 16UC1 millimetres, 1.0 for 32FC1 metres.
    """
    if depth is None or depth.size == 0:
        return None

    rgb_h, rgb_w = rgb_shape[0], rgb_shape[1]
    depth_h, depth_w = depth.shape[0], depth.shape[1]
    sx = depth_w / float(rgb_w)
    sy = depth_h / float(rgb_h)

    x1 = int(np.clip(round(bbox.x1 * sx), 0, depth_w - 1))
    x2 = int(np.clip(round(bbox.x2 * sx), 0, depth_w))
    y1 = int(np.clip(round(bbox.y1 * sy), 0, depth_h - 1))
    y2 = int(np.clip(round(bbox.y2 * sy), 0, depth_h))
    if x2 <= x1 or y2 <= y1:
        return None

    patch = depth[y1:y2, x1:x2].astype(np.float64, copy=False)
    valid = patch[np.isfinite(patch) & (patch > 0)]
    if valid.size == 0:
        return None
    return float(np.median(valid)) * depth_scale


def gate_detections(raws: Sequence[RawDetection], rgb_shape: tuple,
                    depth: Optional[np.ndarray], depth_scale: float,
                    config: GateConfig) -> Optional[Detection]:
    """Return the most central detection that passes every gate, or None."""
    rgb_h, rgb_w = rgb_shape[0], rgb_shape[1]
    if rgb_w <= 0 or rgb_h <= 0:
        return None

    best: Optional[Detection] = None
    for raw in raws:
        if raw.confidence < config.confidence_min:
            continue
        if raw.bbox.width <= 0 or raw.bbox.height <= 0:
            continue

        offset = raw.bbox.cx / float(rgb_w) - 0.5
        if abs(offset) > config.center_tolerance:
            continue

        distance = median_depth_m(depth, raw.bbox, rgb_shape, depth_scale)
        if distance is None:
            continue
        if not (config.distance_min_m <= distance <= config.distance_max_m):
            continue

        candidate = Detection(bbox=raw.bbox, confidence=raw.confidence,
                              distance_m=distance, center_offset=offset)
        if best is None or abs(candidate.center_offset) < abs(best.center_offset):
            best = candidate
    return best
