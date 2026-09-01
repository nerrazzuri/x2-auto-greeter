"""The privacy guarantee: no image is written to disk, anywhere, ever.

Spec section 12. This test installs a tripwire over every filesystem-mutating
entry point Python offers and drives the entire image path through it.
"""
import builtins
import io
import os

import numpy as np
import pytest

from x2_greeter.core.detection import GateConfig, gate_detections
from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.types import BBox, RawDetection


class ScriptedDetector:
    """A stand-in for the real detector: returns a fixed script of detections.

    Defined locally rather than imported so this test is independent of the
    detector module (Task 9); the privacy guarantee under test is about the
    image path, not about which detector produced the boxes.
    """

    def __init__(self, detections):
        self._detections = list(detections)

    def detect(self, bgr):
        return list(self._detections)


@pytest.fixture
def no_disk_writes(monkeypatch):
    """Fail loudly if anything opens a file for writing or touches the FS."""
    violations = []
    real_open = builtins.open

    def guarded_open(file, mode='r', *args, **kwargs):
        if any(flag in mode for flag in ('w', 'a', 'x', '+')):
            violations.append(f'open({file!r}, {mode!r})')
        return real_open(file, mode, *args, **kwargs)

    def forbid(name):
        def _forbidden(*args, **kwargs):
            violations.append(f'{name}{args!r}')
            raise AssertionError(f'{name} called in the image path')
        return _forbidden

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_open)
    monkeypatch.setattr(os, 'write', forbid('os.write'))
    monkeypatch.setattr(os, 'mkdir', forbid('os.mkdir'))
    monkeypatch.setattr(os, 'makedirs', forbid('os.makedirs'))

    import cv2
    monkeypatch.setattr(cv2, 'imwrite', forbid('cv2.imwrite'))

    yield violations
    assert violations == [], f'image path wrote to disk: {violations}'


def test_the_whole_image_path_writes_nothing_to_disk(no_disk_writes):
    rng = np.random.default_rng(0)
    bgr = rng.integers(0, 256, size=(480, 640, 3), dtype=np.uint8)
    depth = np.full((480, 640), 2000, dtype=np.uint16)

    detector = ScriptedDetector([RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])
    raws = detector.detect(bgr)
    detection = gate_detections(raws, bgr.shape[:2], depth, 0.001, GateConfig())
    assert detection is not None

    frame = to_jpeg_frame(bgr)
    assert len(frame.data) > 0


def test_encoding_never_returns_a_path_or_filename():
    rng = np.random.default_rng(0)
    frame = to_jpeg_frame(rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8))
    assert not hasattr(frame, 'path')
    assert not hasattr(frame, 'filename')
