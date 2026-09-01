"""Local person detectors.

The local detector is a *gate*, not a decision: it chooses when the robot is
allowed to spend a cloud call, and the cloud makes the actual call on whether
somebody is really there. That is why a modest detector is acceptable here.
"""
from __future__ import annotations

import logging
import math
import os
from typing import List, Optional, Sequence

import cv2
import numpy as np

from x2_greeter.core.detection import PersonDetector
from x2_greeter.core.types import BBox, RawDetection

# PASCAL VOC class index used by MobileNet-SSD.
PERSON_CLASS_ID = 15
MODEL_FILES = ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')


def parse_ssd_output(raw: np.ndarray, frame_width: int, frame_height: int,
                     class_id: int = PERSON_CLASS_ID) -> List[RawDetection]:
    """Turn a cv2.dnn SSD forward() blob into person boxes in frame pixels.

    The blob has shape (1, 1, N, 7); each row is
    [image_index, class_id, confidence, x1, y1, x2, y2] with the coordinates
    normalised to 0..1. Split out from the detector so it can be tested
    without a 23 MB weights file.
    """
    results: List[RawDetection] = []
    if raw is None or raw.size == 0:
        return results

    rows = raw.reshape(-1, raw.shape[-1])
    for row in rows:
        if int(row[1]) != class_id:
            continue
        x1 = int(np.clip(round(float(row[3]) * frame_width), 0, frame_width))
        y1 = int(np.clip(round(float(row[4]) * frame_height), 0, frame_height))
        x2 = int(np.clip(round(float(row[5]) * frame_width), 0, frame_width))
        y2 = int(np.clip(round(float(row[6]) * frame_height), 0, frame_height))
        if x2 <= x1 or y2 <= y1:
            continue
        results.append(RawDetection(bbox=BBox(x1, y1, x2, y2), confidence=float(row[2])))
    return results


def hog_weight_to_confidence(weight: float) -> float:
    """Squash an SVM decision value into a 0..1 confidence.

    HOG returns a signed distance from the decision boundary rather than a
    probability, so 0.0 maps to 0.5 and the gate's confidence_min keeps its
    usual meaning.
    """
    return 1.0 / (1.0 + math.exp(-float(weight)))


class MobileNetSsdDetector:
    """The configured detector: a Caffe MobileNet-SSD run through cv2.dnn."""

    def __init__(self, prototxt_path: str, model_path: str, input_size=(300, 300)) -> None:
        self._net = cv2.dnn.readNetFromCaffe(str(prototxt_path), str(model_path))
        self._input_size = tuple(input_size)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        height, width = bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(bgr, self._input_size), 0.007843, self._input_size, 127.5)
        self._net.setInput(blob)
        return parse_ssd_output(self._net.forward(), width, height)


class HogPersonDetector:
    """The no-download fallback: OpenCV's built-in HOG pedestrian detector.

    Weaker and slower than MobileNet-SSD, but it ships inside opencv-python,
    so a fresh checkout runs without fetching anything.
    """

    def __init__(self, win_stride=(8, 8), padding=(8, 8), scale: float = 1.05) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._win_stride = tuple(win_stride)
        self._padding = tuple(padding)
        self._scale = float(scale)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        rects, weights = self._hog.detectMultiScale(
            bgr, winStride=self._win_stride, padding=self._padding, scale=self._scale)
        results: List[RawDetection] = []
        for (x, y, w, h), weight in zip(rects, np.ravel(weights)):
            results.append(RawDetection(
                bbox=BBox(int(x), int(y), int(x) + int(w), int(y) + int(h)),
                confidence=hog_weight_to_confidence(weight)))
        return results


class ScriptedDetector:
    """Reports exactly what it is told to. For simulation and integration tests.

    A synthetic camera frame contains no real person, so a real detector would
    never fire on one; this makes the ROS integration test deterministic.
    """

    def __init__(self, detections: Sequence[RawDetection] = (),
                 start_after_frames: int = 0) -> None:
        self._detections = list(detections)
        self._start_after_frames = int(start_after_frames)
        self._frame_count = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def set_detections(self, detections: Sequence[RawDetection]) -> None:
        self._detections = list(detections)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        self._frame_count += 1
        if self._frame_count <= self._start_after_frames:
            return []
        return list(self._detections)


def build_detector(kind: str, model_dir: str,
                   logger: Optional[logging.Logger] = None) -> PersonDetector:
    """Construct the configured detector, degrading rather than refusing to start."""
    log = logger or logging.getLogger(__name__)

    if kind == 'scripted':
        return ScriptedDetector()
    if kind == 'hog':
        return HogPersonDetector()
    if kind != 'mobilenet_ssd':
        raise ValueError(f'unknown detector {kind!r}; expected mobilenet_ssd, hog or scripted')

    prototxt = os.path.join(model_dir, MODEL_FILES[0])
    weights = os.path.join(model_dir, MODEL_FILES[1])
    missing = [p for p in (prototxt, weights) if not os.path.isfile(p)]
    if missing:
        log.warning(
            'MobileNet-SSD weights not found (%s); falling back to the built-in HOG '
            'pedestrian detector. Run tools/fetch_model.py to install them.',
            ', '.join(os.path.basename(p) for p in missing))
        return HogPersonDetector()
    return MobileNetSsdDetector(prototxt, weights)
