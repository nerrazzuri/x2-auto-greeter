"""Pure-Python stand-ins for the ports, for tests and for the simulator.

Deliberately free of rclpy: the host test suite imports this module, and a
single ROS import here would break every host test on a machine without a ROS
installation. The ROS-side simulator is sim/fake_robot.py and stays separate.

Later tasks add fakes for the remaining ports to this file.
"""
from __future__ import annotations

from typing import List, Sequence

from x2_greeter.cognition.transcriber import TranscriptionUnavailable, Utterance


class ScriptedTranscriber:
    """Returns a prepared utterance per call, then empties (or fails)."""

    name = 'scripted'

    def __init__(self, utterances: Sequence[Utterance],
                 raise_when_exhausted: bool = False) -> None:
        self._queue: List[Utterance] = list(utterances)
        self._raise_when_exhausted = bool(raise_when_exhausted)
        self.calls = 0

    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        self.calls += 1
        if self._queue:
            return self._queue.pop(0)
        if self._raise_when_exhausted:
            raise TranscriptionUnavailable('scripted transcriber exhausted')
        return Utterance(text='', language=None, confidence=0.0)
