"""Head-camera RGB-D intake.

Pairs the RGB and depth streams by timestamp and hands the consumer plain
numpy arrays, so nothing downstream needs to know about ROS. Depth is
mandatory: distance gating is a safety rule, and an unpaired RGB frame is
silently dropped rather than gated on a guessed distance.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from cv_bridge import CvBridge
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from x2_greeter.core.stream_health import StreamHealth

FrameCallback = Callable[[np.ndarray, Optional[np.ndarray], float], None]


def _stamp_seconds(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def rotate_180(image: np.ndarray) -> np.ndarray:
    """Turn an image half a turn, returning a contiguous array.

    `image[::-1, ::-1]` alone is a reversed *view*; cv2.dnn (and anything else
    reading the buffer directly) needs contiguous memory, so the copy is not
    optional. Rows and columns are both reversed and the channel axis, when
    there is one, is left alone -- reversing it too would silently swap the
    colour order on top of the rotation.
    """
    return np.ascontiguousarray(image[::-1, ::-1])


class FrameSource:
    def __init__(self, node, rgb_topic: str, depth_topic: str, on_frame: FrameCallback,
                 max_sync_skew_s: float = 0.15, stale_warn_s: float = 5.0,
                 rotate_180: bool = False, callback_group=None) -> None:
        self._node = node
        self._on_frame = on_frame
        self._max_sync_skew_s = float(max_sync_skew_s)
        self._rotate_180 = bool(rotate_180)
        self._bridge = CvBridge()
        self._latest_depth = None
        self._latest_depth_stamp = 0.0
        self._frames_seen = 0
        # Judged on pairs, not colour frames: see core/stream_health.py.
        self._health = StreamHealth(stale_warn_s, started_at=self._now())

        self._rgb_sub = node.create_subscription(
            Image, rgb_topic, self._on_rgb, qos_profile_sensor_data,
            callback_group=callback_group)
        self._depth_sub = node.create_subscription(
            Image, depth_topic, self._on_depth, qos_profile_sensor_data,
            callback_group=callback_group)
        self._watchdog = node.create_timer(1.0, self._check_stale,
                                           callback_group=callback_group)

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def destroy(self) -> None:
        self._node.destroy_timer(self._watchdog)
        self._node.destroy_subscription(self._rgb_sub)
        self._node.destroy_subscription(self._depth_sub)

    def _on_depth(self, msg: Image) -> None:
        try:
            depth = self._bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        except Exception as exc:                       # noqa: BLE001 - a bad frame is not fatal
            self._node.get_logger().warning(f'could not convert depth frame: {exc}')
            return
        # Rotated with the RGB frame or not at all. A bounding box found in the
        # rotated colour image is read against this array, so rotating one and
        # not the other would point every distance lookup at the diagonally
        # opposite corner of the scene -- and that distance is what the 1.0 m
        # arm's-reach interlock is made of.
        self._latest_depth = rotate_180(depth) if self._rotate_180 else depth
        self._latest_depth_stamp = _stamp_seconds(msg)
        self._health.depth(self._now())

    def _on_rgb(self, msg: Image) -> None:
        try:
            bgr = self._bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as exc:                       # noqa: BLE001 - a bad frame is not fatal
            self._node.get_logger().warning(f'could not convert RGB frame: {exc}')
            return
        if self._rotate_180:
            bgr = rotate_180(bgr)

        stamp = _stamp_seconds(msg)
        now = self._now()
        self._health.rgb(now)

        depth = self._latest_depth
        if depth is None or abs(stamp - self._latest_depth_stamp) > self._max_sync_skew_s:
            # No usable depth for this frame. Gating would have to guess the
            # distance, so drop it instead (spec section 6).
            return

        self._frames_seen += 1
        self._paired(now)
        self._on_frame(bgr, depth, stamp)

    def _check_stale(self) -> None:
        warning = self._health.check(self._now())
        if warning is not None:
            self._node.get_logger().warning(warning)

    def _paired(self, now: float) -> None:
        if self._health.pair(now):
            self._node.get_logger().info('RGB-D frames are pairing again')

    def _now(self) -> float:
        return self._node.get_clock().now().nanoseconds * 1e-9
