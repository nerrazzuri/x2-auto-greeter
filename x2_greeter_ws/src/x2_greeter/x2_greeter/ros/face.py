"""The robot's face.

Fire-and-forget: every method returns a bool the caller may ignore, and
nothing here raises. A failed expression is not a failed turn -- the words
are the turn.

Ships disabled. Nothing on this path has run on hardware yet; the flag gets
flipped on site (在现场再做调整).

Corrected against the SDK's own PlayEmoji.srv (Ruling R14), not the design
doc's prose:
- SERVICE is '/aimdk_5Fmsgs/srv/PlayEmoji', matching the
  '/aimdk_5Fmsgs/srv/<Type>' convention every other service in this package
  uses (ros/speech.py, ros/gesture.py, ros/agent_mode.py).
- The response carries header: CommonResponse, bool success, string message
  -- an explicit success flag, not a code to compare against zero.
  header.code defaults to zero, so checking only that would read every
  unpopulated response as success; response.success is checked instead, and
  a refusal is logged with response.message when the vendor supplies one.
- The request also carries int32 priority, which is left at its default (0);
  only emotion_id and mode are set here, as documented.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import rclpy
from aimdk_msgs.srv import PlayEmoji

from x2_greeter.core.faces import CATALOGUE, MODE_ONCE, THINKING

SERVICE = '/aimdk_5Fmsgs/srv/PlayEmoji'
IDLE_EXPRESSION = 'blink'


def _default_spin(node, future, timeout_sec=None):
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)


class Face:
    def __init__(self, node, service: str = SERVICE, enabled: bool = False,
                 timeout_s: float = 1.0, attempts: int = 4,
                 retry_s: float = 0.25, spin_until: Optional[Callable] = None,
                 callback_group=None) -> None:
        self._node = node
        self._enabled = bool(enabled)
        self._timeout_s = float(timeout_s)
        self._attempts = int(attempts)
        self._retry_s = float(retry_s)
        self._spin = spin_until or _default_spin
        self.available = False
        self.last_shown: Optional[str] = None
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._client = node.create_client(PlayEmoji, service, **kwargs)

    def show_thinking(self) -> bool:
        """Shown at t=0.05 s, long before the reply exists.

        This is the whole of the latency mitigation: without it the robot
        stands motionless for two seconds after somebody speaks, which
        reads as broken rather than as thoughtful.
        """
        return self.show(THINKING)

    def clear(self) -> bool:
        return self.show(IDLE_EXPRESSION)

    def show(self, name: str, mode: int = MODE_ONCE) -> bool:
        if not self._enabled:
            return False
        spec = CATALOGUE.get(name)
        if spec is None:
            self._node.get_logger().warn(f'no such expression: {name}')
            return False
        if not self._wait():
            return False

        request = PlayEmoji.Request()
        request.emotion_id = spec.emotion_id
        request.mode = int(mode)
        # priority is left at its .srv default (0); nothing here has a
        # reason to invent a value for it.

        try:
            future = self._client.call_async(request)
            self._spin(self._node, future, timeout_sec=self._timeout_s)
            response = future.result()
        except Exception as exc:        # noqa: BLE001
            self._node.get_logger().warn(
                f'expression {name} failed: {type(exc).__name__}')
            return False

        if response is None:
            self._node.get_logger().warn(f'expression {name} timed out')
            return False

        if not response.success:
            message = getattr(response, 'message', '') or ''
            suffix = f': {message}' if message else ''
            self._node.get_logger().warn(f'expression {name} refused{suffix}')
            return False

        self.available = True
        self.last_shown = name
        return True

    def _wait(self) -> bool:
        for _ in range(self._attempts):
            if self._client.wait_for_service(timeout_sec=self._retry_s):
                return True
            if self._retry_s:
                time.sleep(self._retry_s)
        self._node.get_logger().warn('the emoji service is not available')
        return False
