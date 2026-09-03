"""The wide base frame: one photograph of the room, per session.

Held only until it is handed to the caller. Like every other image in this
package it is never written to disk and never logged.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from x2_greeter.ros.frame_source import rotate_180 as _rotate_180

FrameCallback = Callable[[np.ndarray, float], None]


class EnvCamera:
    def __init__(self, node, topic: str, on_frame: FrameCallback,
                 rotate_180: bool = False, callback_group=None) -> None:
        self._node = node
        self._on_frame = on_frame
        self._rotate = bool(rotate_180)
        self._pending = False
        self.frames_seen = 0
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._sub = node.create_subscription(
            Image, topic, self._on_image, qos_profile_sensor_data, **kwargs)

    @property
    def pending(self) -> bool:
        return self._pending

    def capture_next(self) -> None:
        """Take the next usable frame that arrives, and only that one."""
        self._pending = True

    def destroy(self) -> None:
        self._node.destroy_subscription(self._sub)

    def _on_image(self, msg: Image) -> None:
        self.frames_seen += 1
        if not self._pending:
            return
        image = self._decode(msg)
        if image is None:
            return                      # stay pending; try the next one
        self._pending = False
        if self._rotate:
            image = _rotate_180(image)
        try:
            self._on_frame(image, self._now())
        except Exception as exc:        # noqa: BLE001
            self._node.get_logger().error(
                f'base-frame handler failed: {type(exc).__name__}')

    def _decode(self, msg: Image) -> Optional[np.ndarray]:
        expected = msg.height * msg.width * 3
        buffer = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        if buffer.size != expected:
            # Never log the buffer -- shapes only.
            self._node.get_logger().warn(
                f'dropping a {buffer.size}-byte frame that declares '
                f'{msg.height}x{msg.width}x3')
            return None
        return buffer.reshape((msg.height, msg.width, 3))

    def _now(self) -> float:
        return self._node.get_clock().now().nanoseconds * 1e-9
