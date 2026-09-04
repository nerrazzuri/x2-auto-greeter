"""Head-stereo colour paired with chin-RGBD depth, reprojected to match it.

Same contract as FrameSource -- `on_frame(bgr, depth, stamp)`, depth in uint16
millimetres, an unpaired colour frame dropped rather than gated on a guessed
distance -- so the node can swap one for the other with a config switch and
nothing downstream changes.

Why it exists: on the X2 the chin RGB-D module is mounted low and at a steep
angle, and a standing person lands on the edge of its frame. Measured over 423
frames with somebody standing 1.5 m away, the gate passed on 26% of them, in
bursts with a median length of 0.17 s. The presence tracker needs a continuous
second, so it took 5.1 s to greet anybody. The head stereo pair sees the same
person at confidence 0.94 to 1.00.

The stereo pair has no depth of its own, and computing it from the pair would
mean fisheye rectification and block matching whose error feeds an arm's-reach
interlock. Reprojecting the chin module's structured-light depth avoids all of
that: the transform is exact (both cameras are fixed children of the same URDF
link) and the depth is measured rather than inferred.

What it costs, stated plainly:

  * 10 Hz, the stereo rate, against the depth stream's 30 Hz.
  * Depth covers only where the two fields of view overlap -- the lower part
    of the stereo frame, about 23% of it, with the person's legs and torso in
    it and their head outside. A box that lands entirely outside that region
    gets no distance and is dropped, which is the safe outcome.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from cv_bridge import CvBridge
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

from x2_greeter.core.reprojection import (DepthReprojector, Fisheye, Pinhole,
                                          coverage_fraction, relative_pose)

FrameCallback = Callable[[np.ndarray, Optional[np.ndarray], float], None]


def _stamp_seconds(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class StereoFrameSource:
    def __init__(self, node, rgb_topic: str, rgb_info_topic: str,
                 depth_topic: str, depth_info_topic: str,
                 on_frame: FrameCallback,
                 rgbd_xyz, rgbd_rpy, stereo_xyz, stereo_rpy,
                 max_sync_skew_s: float = 0.15, stale_warn_s: float = 5.0,
                 step: int = 8, scale: int = 4,
                 callback_group=None) -> None:
        self._node = node
        self._on_frame = on_frame
        self._max_sync_skew_s = float(max_sync_skew_s)
        self._stale_warn_s = float(stale_warn_s)
        self._step, self._scale = int(step), int(scale)
        self._bridge = CvBridge()

        self._transform = relative_pose(rgbd_xyz, rgbd_rpy, stereo_xyz, stereo_rpy)
        self._reprojector: Optional[DepthReprojector] = None
        self._source_info: Optional[Pinhole] = None
        self._target_info: Optional[Fisheye] = None
        self._warned_no_calibration = False

        self._latest_depth = None
        self._latest_depth_stamp = 0.0
        self._frames_seen = 0
        self._last_rgb_at: Optional[float] = None
        self._warned_stale = False
        self._logged_coverage = False

        self._rgb_sub = node.create_subscription(
            Image, rgb_topic, self._on_rgb, qos_profile_sensor_data,
            callback_group=callback_group)
        self._depth_sub = node.create_subscription(
            Image, depth_topic, self._on_depth, qos_profile_sensor_data,
            callback_group=callback_group)
        # Calibration arrives on its own topics and is latched here rather than
        # hard-coded: the intrinsics belong to the robot, not to this repo.
        self._rgb_info_sub = node.create_subscription(
            CameraInfo, rgb_info_topic, self._on_rgb_info, qos_profile_sensor_data,
            callback_group=callback_group)
        self._depth_info_sub = node.create_subscription(
            CameraInfo, depth_info_topic, self._on_depth_info,
            qos_profile_sensor_data, callback_group=callback_group)
        self._watchdog = node.create_timer(1.0, self._check_stale,
                                           callback_group=callback_group)

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def destroy(self) -> None:
        self._node.destroy_timer(self._watchdog)
        for sub in (self._rgb_sub, self._depth_sub,
                    self._rgb_info_sub, self._depth_info_sub):
            self._node.destroy_subscription(sub)

    # ---------------------------------------------------------- calibration

    def _on_depth_info(self, msg: CameraInfo) -> None:
        if self._source_info is None:
            self._source_info = Pinhole.from_camera_info(msg.k, msg.width, msg.height)
            self._build_reprojector()

    def _on_rgb_info(self, msg: CameraInfo) -> None:
        if self._target_info is None:
            self._target_info = Fisheye.from_camera_info(msg.k, msg.d,
                                                         msg.width, msg.height)
            self._build_reprojector()

    def _build_reprojector(self) -> None:
        if self._source_info is None or self._target_info is None:
            return
        self._reprojector = DepthReprojector(
            self._source_info, self._target_info, self._transform,
            step=self._step, scale=self._scale)
        self._node.get_logger().info(
            f'reprojecting {self._source_info.width}x{self._source_info.height} depth '
            f'into {self._target_info.width}x{self._target_info.height} colour, '
            f'output {self._reprojector.output_shape}')

    # ------------------------------------------------------------ callbacks

    def _on_depth(self, msg: Image) -> None:
        try:
            depth = self._bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self._node.get_logger().warning(f'could not convert depth frame: {exc}')
            return
        self._latest_depth = depth
        self._latest_depth_stamp = _stamp_seconds(msg)

    def _on_rgb(self, msg: Image) -> None:
        try:
            bgr = self._bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self._node.get_logger().warning(f'could not convert RGB frame: {exc}')
            return

        stamp = _stamp_seconds(msg)
        self._last_rgb_at = self._now()
        self._warned_stale = False

        if self._reprojector is None:
            self._warn_no_calibration()
            return

        depth = self._latest_depth
        if depth is None or abs(stamp - self._latest_depth_stamp) > self._max_sync_skew_s:
            # Same rule as FrameSource: no usable depth means no gating, and
            # gating on a guessed distance is what this refuses to do.
            return

        try:
            aligned = self._reprojector.reproject(depth)
        except ValueError as exc:
            # The depth stream changed shape under the calibration we latched.
            self._node.get_logger().warning(f'could not reproject depth: {exc}')
            return

        if not self._logged_coverage:
            self._logged_coverage = True
            self._node.get_logger().info(
                f'depth covers {100.0 * coverage_fraction(aligned):.0f}% of the '
                f'colour frame; a person outside that region is dropped, not guessed at')

        self._frames_seen += 1
        self._on_frame(bgr, aligned, stamp)

    # -------------------------------------------------------------- watchdog

    def _check_stale(self) -> None:
        if self._last_rgb_at is None or self._warned_stale:
            return
        if (self._now() - self._last_rgb_at) > self._stale_warn_s:
            self._node.get_logger().warning(
                f'no camera frame for over {self._stale_warn_s:.0f}s')
            self._warned_stale = True

    def _warn_no_calibration(self) -> None:
        if self._warned_no_calibration:
            return
        missing = [name for name, value in (('depth', self._source_info),
                                            ('colour', self._target_info))
                   if value is None]
        self._node.get_logger().warning(
            f'no {" and ".join(missing)} camera_info yet; not gating until it '
            f'arrives -- the intrinsics come from the robot, not from config')
        self._warned_no_calibration = True

    def _now(self) -> float:
        return self._node.get_clock().now().nanoseconds * 1e-9
