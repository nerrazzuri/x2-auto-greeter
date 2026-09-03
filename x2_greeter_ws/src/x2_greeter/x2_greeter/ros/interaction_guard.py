"""Is the robot already talking to somebody?

The X2 ships its own interaction system, and it is not the greeter's to
interrupt. A person mid-conversation with the robot does not want a cheerful
"Hello there!" cutting across the answer they were waiting for, and the two
voices arrive on the same speaker.

Two signals, both event-driven, both carrying the source that caused them:

  /interaction/tts_status     TtsStatus       — the interaction module speaking
  /aima/hal/audio/play_state  PlayStateChange — anything playing on the device

Because they are event-driven and not state topics, a missed end-event would
latch "busy" forever and the greeter would go quiet for the rest of the day.
So busy expires: `busy_timeout_s` after the last message that set it, with a
log line, on the principle that a greeter that talks over somebody once is a
smaller failure than one that silently stops working.

The greeter's own speech comes back on these same topics. Ignoring it is not
an optimisation — without it the node would hear itself start speaking, mark
itself busy, and refuse the next greeting for as long as the timeout. The
`domain` and `pkg_name` fields exist for exactly this, and the greeter already
stamps its own domain onto every PlayTts request.
"""
from __future__ import annotations

import threading
from typing import Optional

from aimdk_msgs.msg import PlayStateChange, PlayStateType, TtsStatus, TtsStatusType
from rclpy.qos import qos_profile_sensor_data

DEFAULT_TTS_STATUS_TOPIC = '/interaction/tts_status'
DEFAULT_PLAY_STATE_TOPIC = '/aima/hal/audio/play_state'

#: Speaking, about to speak, or queued to speak. INQUE counts: the robot is
#: committed to saying something and greeting over the top of it is the same
#: interruption a moment later.
TTS_BUSY_STATES = (
    TtsStatusType.TTS_STATUS_TYPE_BEGIN,
    TtsStatusType.TTS_STATUS_TYPE_PLAYING,
    TtsStatusType.TTS_STATUS_TYPE_INQUE,
)


class InteractionGuard:
    """Tracks whether something other than the greeter is using the voice."""

    def __init__(self, node, own_domain: str = 'x2_greeter',
                 tts_status_topic: str = DEFAULT_TTS_STATUS_TOPIC,
                 play_state_topic: str = DEFAULT_PLAY_STATE_TOPIC,
                 busy_timeout_s: float = 15.0,
                 callback_group=None) -> None:
        self._node = node
        self._own_domain = str(own_domain)
        self._busy_timeout_s = float(busy_timeout_s)
        self._lock = threading.Lock()
        self._busy_since: Optional[float] = None
        self._busy_reason: str = ''

        self._tts_sub = node.create_subscription(
            TtsStatus, tts_status_topic, self._on_tts, qos_profile_sensor_data,
            callback_group=callback_group)
        self._play_sub = node.create_subscription(
            PlayStateChange, play_state_topic, self._on_play_state,
            qos_profile_sensor_data, callback_group=callback_group)

    def destroy(self) -> None:
        self._node.destroy_subscription(self._tts_sub)
        self._node.destroy_subscription(self._play_sub)

    # ------------------------------------------------------------- queries

    def busy_reason(self) -> Optional[str]:
        """Why the greeter should stay quiet, or None if it may speak."""
        with self._lock:
            if self._busy_since is None:
                return None
            age = self._now() - self._busy_since
            if age > self._busy_timeout_s:
                stale = self._busy_reason
                self._busy_since = None
                self._busy_reason = ''
                self._node.get_logger().warning(
                    f'no end-of-speech for {age:.0f}s after {stale}; assuming the '
                    f'robot is free again rather than staying quiet indefinitely')
                return None
            return self._busy_reason

    # ------------------------------------------------------------ callbacks

    def _on_tts(self, msg: TtsStatus) -> None:
        if (msg.domain or '').strip() == self._own_domain:
            return                                     # our own greeting
        status = int(msg.tts_status.value)
        if status in TTS_BUSY_STATES:
            self._mark_busy(f'the interaction system is speaking (tts status {status})')
        else:
            self._mark_free()

    def _on_play_state(self, msg: PlayStateChange) -> None:
        if (msg.pkg_name or '').strip() == self._own_domain:
            return
        if int(msg.state.value) == PlayStateType.PLAYER_STATE_PLAYING:
            source = (msg.pkg_name or 'an unnamed source').strip()
            self._mark_busy(f'audio is playing from {source}')
        else:
            self._mark_free()

    # -------------------------------------------------------------- helpers

    def _mark_busy(self, reason: str) -> None:
        with self._lock:
            first = self._busy_since is None
            self._busy_since = self._now()
            self._busy_reason = reason
        if first:
            self._node.get_logger().info(f'holding greetings: {reason}')

    def _mark_free(self) -> None:
        with self._lock:
            was_busy = self._busy_since is not None
            self._busy_since = None
            self._busy_reason = ''
        if was_busy:
            self._node.get_logger().info('interaction finished; greeting again')

    def _now(self) -> float:
        return self._node.get_clock().now().nanoseconds * 1e-9
