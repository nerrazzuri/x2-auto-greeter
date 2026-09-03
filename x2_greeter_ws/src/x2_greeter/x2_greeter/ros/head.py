"""Head yaw. Clamped twice, centred on every exit, disabled by default.

The vendor limit is +/-20 degrees (0.349 rad). Ours is 0.262 rad, enforced
in core.gaze and again here. Two clamps because core.gaze is pure and this
class is what reaches a motor: the day somebody calls look_at with a number
that did not come through gaze.py, this is the only clamp that runs.

The sweep is not a way to see more -- 94 degrees of head camera plus 30 of
sweep is 134, and one front stereo frame already covers 156. It exists
because a robot that glances around before answering reads as attentive.

Corrected against the SDK's own joint_control.html and the JointStateArray/
JointState .msg files, not the task brief's prose: the COMMAND topic
(/aima/hal/joint/head/command) carries JointCommandArray, as documented, but
the STATE topic (/aima/hal/joint/head/state) carries a *different* type,
JointStateArray (header, state: DomainErrorState, joints: JointState[]).
JointState is not JointCommand -- it has name/position/velocity/effort/
error_code, not stiffness/damping. Subscribing with the wrong message type
would build a subscription that can never match what the vendor node
actually publishes on that topic.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from aimdk_msgs.msg import JointCommand, JointCommandArray, JointStateArray
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from x2_greeter.core.gaze import (
    HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD, clamp_yaw, sweep_waypoints)

COMMAND_TOPIC = '/aima/hal/joint/head/command'
STATE_TOPIC = '/aima/hal/joint/head/state'


class Head:
    def __init__(self, node, command_topic: str = COMMAND_TOPIC,
                 state_topic: str = STATE_TOPIC, enabled: bool = False,
                 rate_hz: float = 20.0, callback_group=None) -> None:
        self._node = node
        self._enabled = bool(enabled)
        self._rate_hz = float(rate_hz)
        self.current_yaw = 0.0
        self.measured_yaw: Optional[float] = None
        self.commands_sent = 0

        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._pub = node.create_publisher(
            JointCommandArray, command_topic, 10, **kwargs)

        state_qos = QoSProfile(depth=1)
        state_qos.reliability = ReliabilityPolicy.RELIABLE
        state_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self._state_sub = node.create_subscription(
            JointStateArray, state_topic, self._on_state, state_qos,
            **kwargs)

    # -- commands ---------------------------------------------------------

    def look_at(self, yaw_rad: float) -> bool:
        if not self._enabled:
            return False
        yaw = clamp_yaw(yaw_rad)
        # And again, here, because this is the call that reaches a motor.
        yaw = max(-MAX_YAW_RAD, min(MAX_YAW_RAD, yaw))
        self._publish(yaw)
        return True

    def centre(self) -> bool:
        return self.look_at(0.0)

    def sweep(self, on_capture: Optional[Callable[[float], None]] = None,
              sleep: Optional[Callable[[float], None]] = None) -> bool:
        """Centre, left, hold, right, hold, centre.

        on_capture is called once at each hold, with the yaw it was taken
        at. The head returns to centre on every exit path, including an
        exception out of on_capture -- a head left 15 degrees off-centre is
        how the next session starts by looking at a wall.
        """
        if not self._enabled:
            return False
        rest = sleep if sleep is not None else time.sleep
        try:
            previous_t = 0.0
            for step in sweep_waypoints(rate_hz=self._rate_hz):
                self.look_at(step.yaw)
                rest(max(0.0, step.t_s - previous_t))
                previous_t = step.t_s
                if step.capture and on_capture is not None:
                    on_capture(step.yaw)
            return True
        finally:
            self._force_centre()

    def destroy(self) -> None:
        self._force_centre()
        self._node.destroy_subscription(self._state_sub)
        self._node.destroy_publisher(self._pub)

    # -- internals --------------------------------------------------------

    def _force_centre(self) -> None:
        # Deliberately not look_at(): centring must happen even on the exit
        # path of a head that was disabled mid-flight.
        if self._enabled:
            self._publish(0.0)

    def _publish(self, yaw: float) -> None:
        yaw_entry = JointCommand()
        yaw_entry.name = HEAD_YAW_JOINT
        yaw_entry.position = float(yaw)
        pitch_entry = JointCommand()
        pitch_entry.name = HEAD_PITCH_JOINT
        pitch_entry.position = 0.0      # this phase moves yaw only

        msg = JointCommandArray()
        msg.joints = [yaw_entry, pitch_entry]
        self._pub.publish(msg)
        self.current_yaw = float(yaw)
        self.commands_sent += 1

    def _on_state(self, msg) -> None:
        for entry in getattr(msg, 'joints', ()):
            if entry.name == HEAD_YAW_JOINT:
                self.measured_yaw = float(entry.position)
                return
