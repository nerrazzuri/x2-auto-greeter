"""Pure-Python stand-ins for the ports, for tests and for the simulator.

Deliberately free of rclpy: the host test suite imports this module, and a
single ROS import here would break every host test on a machine without a ROS
installation. The ROS-side simulator is sim/fake_robot.py and stays separate.

Later tasks add fakes for the remaining ports to this file.
"""
from __future__ import annotations

from typing import List, Sequence

from x2_greeter.cognition.dialogue import BackendUnavailable
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


class ScriptedDialogueBackend:
    """Returns prepared turns and remembers what it was asked.

    fail_after=N raises BackendUnavailable from call N+1 onwards, which is how
    the conversation tests exercise the cloud-failure path without a network.
    """

    name = 'scripted'

    def __init__(self, turns, fail_after=None) -> None:
        self._turns = list(turns)
        self._fail_after = fail_after
        self.calls = 0
        self.last_call = None

    def respond(self, base_frame, frame, scene, venue, history, utterance,
                language, child):
        self.calls += 1
        self.last_call = {
            'base_frame': base_frame, 'frame': frame, 'scene': scene,
            'venue': venue, 'history': tuple(history), 'utterance': utterance,
            'language': language, 'child': child,
        }
        if self._fail_after is not None and self.calls > self._fail_after:
            raise BackendUnavailable('scripted failure')
        if not self._turns:
            raise BackendUnavailable('scripted backend exhausted')
        return self._turns.pop(0)
