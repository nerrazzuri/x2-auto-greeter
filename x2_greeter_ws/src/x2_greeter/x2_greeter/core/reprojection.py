"""Putting one camera's depth into another camera's picture.

The X2 carries two forward cameras that are good at different halves of this
job. The chin RGB-D module measures distance well and sees almost nothing of a
standing person -- measured on hardware, it detected nobody at all while the
head stereo pair detected the same person at confidence 0.94 to 1.00. The
stereo pair has the view and no depth at all.

So: detect in the stereo image, and read the distance out of the chin camera's
depth, reprojected into the stereo image's own pixels. Every depth pixel is
unprojected to a 3D point, moved into the stereo camera's frame, and projected
back through the stereo lens. What comes out is a depth map the detector's
bounding boxes can be read against directly.

The transform between the two is exact rather than calibrated: both cameras
are fixed children of `head_pitch_link` in the robot's own URDF, so their
relative pose is a constant and is unaffected by the head moving.

Validated on hardware against a tape measure: a person at 2.0 m read 2.00 m,
and at 1.0 m read 0.89 m over 46 samples with a standard deviation of 0.01 m.
The short reading at 1 m is the body's own thickness -- the tape was to where
the person stood, the camera sees their chest -- and it errs towards *nearer*,
which is the safe direction for a floor that refuses to gesture when somebody
is too close.

No ROS here: the maths is testable without a robot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """URDF's fixed-axis roll-pitch-yaw, i.e. Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def pose_matrix(xyz: Sequence[float], rpy: Sequence[float]) -> np.ndarray:
    """The 4x4 a URDF <origin xyz rpy> describes: parent <- child."""
    matrix = np.eye(4)
    matrix[:3, :3] = rpy_to_matrix(*rpy)
    matrix[:3, 3] = xyz
    return matrix


def relative_pose(from_xyz, from_rpy, to_xyz, to_rpy) -> np.ndarray:
    """T_to<-from, for two links that share a parent.

    Both cameras hang off head_pitch_link, so the shared parent cancels and
    the result is a constant: the head can pitch and yaw without changing it.
    """
    return np.linalg.inv(pose_matrix(to_xyz, to_rpy)) @ pose_matrix(from_xyz, from_rpy)


@dataclass(frozen=True)
class Pinhole:
    """A rectified depth camera. The X2's depth stream ships plumb_bob with
    all-zero coefficients, so there is nothing to undistort."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @classmethod
    def from_camera_info(cls, k, width: int, height: int) -> 'Pinhole':
        k = np.asarray(k, dtype=float).reshape(3, 3)
        return cls(fx=k[0, 0], fy=k[1, 1], cx=k[0, 2], cy=k[1, 2],
                   width=int(width), height=int(height))


@dataclass(frozen=True)
class Fisheye:
    """An equidistant camera, which is what the head stereo pair reports."""

    k: np.ndarray
    d: np.ndarray
    width: int
    height: int

    @classmethod
    def from_camera_info(cls, k, d, width: int, height: int) -> 'Fisheye':
        return cls(k=np.asarray(k, dtype=float).reshape(3, 3),
                   d=np.asarray(d, dtype=float).reshape(-1)[:4].copy(),
                   width=int(width), height=int(height))


class DepthReprojector:
    """Reprojects a depth image from one camera into another camera's pixels.

    `step` subsamples the source depth image and `scale` shrinks the output.
    Both trade fidelity for time, and both are honest about it: the output is
    a sparse splat, not a rendered surface, so a bounding box reads a cloud of
    samples rather than a filled shape. `median_depth_m` already takes a median
    over whatever valid pixels it finds, which is exactly the right thing to do
    with a sparse map.
    """

    def __init__(self, source: Pinhole, target: Fisheye, transform: np.ndarray,
                 step: int = 4, scale: int = 4,
                 min_depth_m: float = 0.2, max_depth_m: float = 6.0) -> None:
        self._source = source
        self._target = target
        self._rotation = np.ascontiguousarray(transform[:3, :3])
        self._translation = np.ascontiguousarray(transform[:3, 3])
        self._step = max(1, int(step))
        self._scale = max(1, int(scale))
        self._min_depth_m = float(min_depth_m)
        self._max_depth_m = float(max_depth_m)

        # The ray through each sampled source pixel never changes, so it is
        # computed once. Only the multiplication by depth happens per frame.
        vs, us = np.mgrid[0:source.height:self._step, 0:source.width:self._step]
        self._rays = np.stack([(us - source.cx) / source.fx,
                               (vs - source.cy) / source.fy,
                               np.ones_like(us, dtype=float)], axis=-1).reshape(-1, 3)
        self.out_height = int(math.ceil(target.height / self._scale))
        self.out_width = int(math.ceil(target.width / self._scale))

    @property
    def output_shape(self) -> Tuple[int, int]:
        return (self.out_height, self.out_width)

    def reproject(self, depth_mm: np.ndarray) -> np.ndarray:
        """Return a uint16 millimetre depth map in the target camera's pixels.

        Millimetres and uint16 deliberately: it is the same contract the raw
        depth topic has, so `camera.depth_scale` and `median_depth_m` keep
        working unchanged and nothing downstream needs to know a reprojection
        happened.
        """
        out = np.zeros(self.output_shape, dtype=np.uint16)
        if depth_mm is None or depth_mm.size == 0:
            return out
        if depth_mm.shape != (self._source.height, self._source.width):
            raise ValueError(
                f'depth image is {depth_mm.shape}, expected '
                f'{(self._source.height, self._source.width)}')

        z = depth_mm[::self._step, ::self._step].astype(np.float32).reshape(-1) * 0.001
        usable = (z > self._min_depth_m) & (z < self._max_depth_m)
        if not usable.any():
            return out

        points = self._rays[usable] * z[usable, None]
        points = points @ self._rotation.T + self._translation

        # Behind the target lens there is no pixel to land on. A fisheye model
        # will happily fold such points back into the image if asked.
        in_front = points[:, 2] > 0.1
        if not in_front.any():
            return out
        points = points[in_front]

        pixels = cv2.fisheye.projectPoints(
            points.reshape(-1, 1, 3).astype(np.float64), np.zeros(3), np.zeros(3),
            self._target.k, self._target.d)[0].reshape(-1, 2)

        xs = np.floor(pixels[:, 0] / self._scale).astype(np.int32)
        ys = np.floor(pixels[:, 1] / self._scale).astype(np.int32)
        inside = ((xs >= 0) & (xs < self.out_width) &
                  (ys >= 0) & (ys < self.out_height))
        if not inside.any():
            return out
        xs, ys, depths = xs[inside], ys[inside], points[inside, 2]

        # Where several source pixels land on one output pixel, keep the
        # nearest. Writing them in far-to-near order leaves the nearest last.
        # Nearest rather than mean because this feeds an arm's-reach floor: if
        # two surfaces share a pixel, the closer one is the one that matters.
        order = np.argsort(-depths)
        out[ys[order], xs[order]] = np.clip(
            depths[order] * 1000.0, 0, np.iinfo(np.uint16).max).astype(np.uint16)
        return out


def coverage_fraction(depth_map: np.ndarray) -> float:
    """Share of the reprojected map that carries a reading. Diagnostic only."""
    if depth_map is None or depth_map.size == 0:
        return 0.0
    return float(np.count_nonzero(depth_map)) / float(depth_map.size)
