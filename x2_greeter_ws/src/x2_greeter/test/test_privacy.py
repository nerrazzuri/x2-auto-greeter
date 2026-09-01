"""The privacy guarantee: no image is written to disk, anywhere, ever.

Spec section 12. This test installs a tripwire over every filesystem-mutating
entry point Python offers and drives the entire image path through it.
"""
import builtins
import io
import os
import pathlib

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


def _install_no_disk_writes_guards(monkeypatch):
    """Install tripwire guards over all filesystem-mutating entry points.

    Returns the violations list that accumulates recorded violations.
    Shared by the no_disk_writes fixture and the tripwire self-test.
    """
    violations = []
    real_open = builtins.open
    real_path_open = pathlib.Path.open
    real_path_write_bytes = pathlib.Path.write_bytes
    real_path_write_text = pathlib.Path.write_text

    def guarded_open(file, mode='r', *args, **kwargs):
        if any(flag in mode for flag in ('w', 'a', 'x', '+')):
            violations.append(f'open({file!r}, {mode!r})')
        return real_open(file, mode, *args, **kwargs)

    def guarded_path_open(self, mode='r', *args, **kwargs):
        if any(flag in mode for flag in ('w', 'a', 'x', '+')):
            violations.append(f'Path.open({str(self)!r}, {mode!r})')
        return real_path_open(self, mode, *args, **kwargs)

    def forbid(name):
        def _forbidden(*args, **kwargs):
            violations.append(f'{name}{args!r}')
            raise AssertionError(f'{name} called in the image path')
        return _forbidden

    def forbid_path_write_bytes(*args, **kwargs):
        violations.append(f'Path.write_bytes{args!r}')
        raise AssertionError('Path.write_bytes called in the image path')

    def forbid_path_write_text(*args, **kwargs):
        violations.append(f'Path.write_text{args!r}')
        raise AssertionError('Path.write_text called in the image path')

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_open)
    monkeypatch.setattr(pathlib.Path, 'open', guarded_path_open)
    monkeypatch.setattr(pathlib.Path, 'write_bytes', forbid_path_write_bytes)
    monkeypatch.setattr(pathlib.Path, 'write_text', forbid_path_write_text)
    monkeypatch.setattr(os, 'write', forbid('os.write'))
    monkeypatch.setattr(os, 'mkdir', forbid('os.mkdir'))
    monkeypatch.setattr(os, 'makedirs', forbid('os.makedirs'))

    import cv2
    monkeypatch.setattr(cv2, 'imwrite', forbid('cv2.imwrite'))

    return violations


@pytest.fixture
def no_disk_writes(monkeypatch):
    """Fail loudly if anything opens a file for writing or touches the FS."""
    violations = _install_no_disk_writes_guards(monkeypatch)
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


def test_the_tripwire_itself_catches_a_write(monkeypatch, tmp_path):
    """Verify the tripwire guards actually catch write attempts.

    A privacy guard that cannot fail is worthless. This test exercises each
    filesystem-mutating entry point to confirm the guards intercept it. If
    this test fails, it means the tripwire has a hole and the main image-path
    tests cannot be trusted.
    """
    violations = _install_no_disk_writes_guards(monkeypatch)

    # Test 1: builtins.open with 'wb' mode
    try:
        with open(tmp_path / 'test.jpg', 'wb') as f:
            f.write(b'secret')
        assert False, "builtins.open('wb') should have been caught"
    except AssertionError:
        pass  # Expected
    assert any("open(" in v for v in violations), f"No open() violation recorded: {violations}"
    violations.clear()

    # Test 2: pathlib.Path.write_bytes
    try:
        (tmp_path / 'test2.jpg').write_bytes(b'secret image bytes')
        assert False, "Path.write_bytes should have been caught"
    except AssertionError as e:
        assert 'Path.write_bytes called in the image path' in str(e)
    assert any("Path.write_bytes" in v for v in violations), f"No Path.write_bytes violation recorded: {violations}"
    violations.clear()

    # Test 3: pathlib.Path.open with 'wb' mode
    try:
        with (tmp_path / 'test3.jpg').open('wb') as f:
            f.write(b'secret')
        assert False, "Path.open('wb') should have been caught"
    except AssertionError:
        pass  # Expected
    assert any("Path.open" in v for v in violations), f"No Path.open violation recorded: {violations}"
    violations.clear()

    # Test 4: cv2.imwrite
    try:
        import cv2
        cv2.imwrite(str(tmp_path / 'test4.jpg'), np.zeros((10, 10, 3), dtype=np.uint8))
        assert False, "cv2.imwrite should have been caught"
    except AssertionError as e:
        assert 'cv2.imwrite called in the image path' in str(e)
    assert any("cv2.imwrite" in v for v in violations), f"No cv2.imwrite violation recorded: {violations}"
