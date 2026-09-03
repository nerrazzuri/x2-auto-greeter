"""The vendor audio source: VAD-segmented speech off a ROS topic.

One utterance is everything between a BEGIN and its matching END. Audio
lives in memory for exactly that long and is then dropped -- never written
to disk, never logged, not at any level. That rule is the same one the
images live under, and it is enforced by test/test_privacy.py.
"""
from __future__ import annotations

from typing import Callable, Optional

from aimdk_msgs.msg import AudioVadStateType, ProcessedAudioOutput
from rclpy.qos import QoSProfile, ReliabilityPolicy

from x2_greeter.cognition.transcriber import SAMPLE_RATE_HZ

BYTES_PER_SAMPLE = 2                    # 16-bit mono PCM, S16LE
DEFAULT_TOPIC = '/agent/process_audio_output'

UtteranceCallback = Callable[[bytes, float], None]


class VendorAudioSource:
    def __init__(self, node, on_utterance: UtteranceCallback,
                 topic: str = DEFAULT_TOPIC, max_utterance_s: float = 15.0,
                 qos_depth: int = 20, callback_group=None) -> None:
        self._node = node
        self._on_utterance = on_utterance
        self._max_bytes = int(max_utterance_s * SAMPLE_RATE_HZ
                              * BYTES_PER_SAMPLE)
        self._buffer = bytearray()
        self._stream_id: Optional[int] = None
        self._running = False
        self.utterances_seen = 0

        qos = QoSProfile(depth=qos_depth)
        qos.reliability = ReliabilityPolicy.RELIABLE
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._sub = node.create_subscription(
            ProcessedAudioOutput, topic, self._on_chunk, qos, **kwargs)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._discard()
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._discard()

    def destroy(self) -> None:
        self.stop()
        self._node.destroy_subscription(self._sub)

    @property
    def bytes_buffered(self) -> int:
        return len(self._buffer)

    # -- internals --------------------------------------------------------

    def _discard(self) -> None:
        self._buffer = bytearray()
        self._stream_id = None

    def _on_chunk(self, msg: ProcessedAudioOutput) -> None:
        if not self._running:
            return
        state = msg.audio_vad_state.value
        payload = bytes(bytearray(msg.audio_data))

        if state == AudioVadStateType.AUDIO_VAD_STATE_BEGIN:
            # A new BEGIN supersedes anything unfinished: either the vendor
            # gave up on the last utterance or a different stream took over.
            self._discard()
            self._stream_id = msg.stream_id
            self._append(payload)
            return

        if self._stream_id is None:
            return                      # joined mid-utterance; drop it
        if msg.stream_id != self._stream_id:
            return                      # a stream we are not following

        self._append(payload)
        if state == AudioVadStateType.AUDIO_VAD_STATE_END:
            self._emit()

    def _append(self, payload: bytes) -> None:
        if len(self._buffer) + len(payload) > self._max_bytes:
            # Somebody is monologuing, or END never came. Neither is a turn.
            self._node.get_logger().warn(
                'utterance exceeded the length limit, discarding')
            self._discard()
            return
        self._buffer.extend(payload)

    def _emit(self) -> None:
        pcm = bytes(self._buffer)
        self._discard()
        if not pcm:
            return
        self.utterances_seen += 1
        try:
            self._on_utterance(pcm, self._now())
        except Exception as exc:        # noqa: BLE001
            # A downstream failure must not take the subscription with it.
            # The exception type only -- never the audio.
            self._node.get_logger().error(
                f'utterance handler failed: {type(exc).__name__}')

    def _now(self) -> float:
        clock = getattr(self._node, 'get_clock', None)
        if clock is None:
            return 0.0
        return clock().now().nanoseconds / 1e9
