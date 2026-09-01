import numpy as np
import pytest

from x2_greeter.core.detection import PersonDetector
from x2_greeter.core.detectors import (
    MODEL_FILES,
    HogPersonDetector,
    ScriptedDetector,
    build_detector,
    hog_weight_to_confidence,
    parse_ssd_output,
)
from x2_greeter.core.types import BBox, RawDetection


def ssd_row(class_id, confidence, x1, y1, x2, y2):
    return [0.0, float(class_id), float(confidence), x1, y1, x2, y2]


def ssd_blob(rows):
    return np.array([[rows]], dtype=np.float32)  # shape (1, 1, N, 7)


def test_ssd_output_is_scaled_to_frame_pixels():
    blob = ssd_blob([ssd_row(15, 0.9, 0.25, 0.1, 0.75, 0.9)])
    got = parse_ssd_output(blob, frame_width=640, frame_height=480)
    assert got == [RawDetection(bbox=BBox(160, 48, 480, 432), confidence=pytest.approx(0.9))]


def test_ssd_output_keeps_only_the_person_class():
    blob = ssd_blob([
        ssd_row(7, 0.99, 0.1, 0.1, 0.2, 0.2),    # car
        ssd_row(15, 0.8, 0.3, 0.1, 0.6, 0.9),    # person
        ssd_row(12, 0.95, 0.7, 0.1, 0.9, 0.4),   # dog
    ])
    got = parse_ssd_output(blob, 640, 480)
    assert len(got) == 1
    assert got[0].confidence == pytest.approx(0.8)


def test_ssd_boxes_are_clamped_to_the_frame():
    blob = ssd_blob([ssd_row(15, 0.9, -0.2, -0.3, 1.4, 1.9)])
    box = parse_ssd_output(blob, 640, 480)[0].bbox
    assert (box.x1, box.y1, box.x2, box.y2) == (0, 0, 640, 480)


def test_ssd_degenerate_boxes_are_dropped():
    blob = ssd_blob([ssd_row(15, 0.9, 0.5, 0.5, 0.5, 0.5)])
    assert parse_ssd_output(blob, 640, 480) == []


def test_ssd_empty_output_yields_nothing():
    assert parse_ssd_output(np.zeros((1, 1, 0, 7), dtype=np.float32), 640, 480) == []


def test_hog_weight_zero_is_the_decision_boundary():
    assert hog_weight_to_confidence(0.0) == pytest.approx(0.5)


def test_hog_confidence_is_monotonic_and_bounded():
    values = [hog_weight_to_confidence(w) for w in (-4.0, -1.0, 0.0, 1.0, 4.0)]
    assert values == sorted(values)
    assert all(0.0 < v < 1.0 for v in values)


def test_hog_detector_satisfies_the_protocol_and_runs_on_a_blank_frame():
    detector = HogPersonDetector()
    assert isinstance(detector, PersonDetector)
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(blank) == []


def test_scripted_detector_returns_what_it_was_given():
    wanted = [RawDetection(bbox=BBox(10, 10, 50, 150), confidence=0.9)]
    detector = ScriptedDetector(wanted)
    assert isinstance(detector, PersonDetector)
    assert detector.detect(np.zeros((240, 320, 3), dtype=np.uint8)) == wanted


def test_scripted_detector_can_stay_quiet_for_a_while():
    wanted = [RawDetection(bbox=BBox(10, 10, 50, 150), confidence=0.9)]
    detector = ScriptedDetector(wanted, start_after_frames=2)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(frame) == []
    assert detector.detect(frame) == []
    assert detector.detect(frame) == wanted
    assert detector.frame_count == 3


def test_scripted_detector_can_be_reprogrammed():
    detector = ScriptedDetector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(frame) == []
    wanted = [RawDetection(bbox=BBox(0, 0, 10, 10), confidence=0.7)]
    detector.set_detections(wanted)
    assert detector.detect(frame) == wanted


def test_build_detector_returns_the_scripted_detector_on_request():
    assert isinstance(build_detector('scripted', ''), ScriptedDetector)


def test_build_detector_returns_hog_on_request():
    assert isinstance(build_detector('hog', ''), HogPersonDetector)


def test_build_detector_falls_back_to_hog_when_the_weights_are_missing(tmp_path, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        detector = build_detector('mobilenet_ssd', str(tmp_path), logger=logging.getLogger('t'))
    assert isinstance(detector, HogPersonDetector)
    assert 'MobileNetSSD_deploy.caffemodel' in caplog.text
    assert 'tools/fetch_model.py' in caplog.text


def test_build_detector_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match='unknown detector'):
        build_detector('yolo', '')


def test_the_expected_model_filenames_are_declared():
    assert MODEL_FILES == ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')
