import numpy as np
import pytest

from x2_greeter.core.detection import GateConfig, gate_detections, median_depth_m, person_distances
from x2_greeter.core.types import BBox, RawDetection

RGB_SHAPE = (480, 640)  # (height, width)


def uniform_depth(value_mm, shape=RGB_SHAPE):
    return np.full(shape, value_mm, dtype=np.uint16)


def centred_box(width=100, height=300):
    cx, cy = RGB_SHAPE[1] // 2, RGB_SHAPE[0] // 2
    return BBox(cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)


def test_median_depth_converts_millimetres_to_metres():
    depth = uniform_depth(2000)
    assert median_depth_m(depth, centred_box(), RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_handles_a_float_metre_encoding():
    depth = np.full(RGB_SHAPE, 2.5, dtype=np.float32)
    assert median_depth_m(depth, centred_box(), RGB_SHAPE, 1.0) == pytest.approx(2.5)


def test_median_depth_scales_the_box_when_depth_resolution_differs():
    # Depth is half the RGB resolution; only the true box region holds 2000 mm.
    depth = np.zeros((240, 320), dtype=np.uint16)
    box = centred_box()
    depth[box.y1 // 2:box.y2 // 2, box.x1 // 2:box.x2 // 2] = 2000
    assert median_depth_m(depth, box, RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_ignores_zero_pixels():
    depth = uniform_depth(2000)
    box = centred_box()
    depth[box.y1:box.y1 + 200, box.x1:box.x2] = 0  # a large invalid patch
    assert median_depth_m(depth, box, RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_ignores_nan_and_inf():
    depth = np.full(RGB_SHAPE, 2.0, dtype=np.float32)
    box = centred_box()
    depth[box.y1, box.x1] = np.nan
    depth[box.y1 + 1, box.x1] = np.inf
    assert median_depth_m(depth, box, RGB_SHAPE, 1.0) == pytest.approx(2.0)


def test_median_depth_is_none_when_every_pixel_is_invalid():
    assert median_depth_m(uniform_depth(0), centred_box(), RGB_SHAPE, 0.001) is None


def test_median_depth_is_none_when_there_is_no_depth_frame():
    assert median_depth_m(None, centred_box(), RGB_SHAPE, 0.001) is None


def test_a_good_detection_passes_every_gate():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    got = gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.confidence == 0.9
    assert got.distance_m == pytest.approx(2.0)
    assert got.center_offset == pytest.approx(0.0)


def test_low_confidence_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.4)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_someone_too_far_away_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(5000), 0.001, GateConfig()) is None


def test_someone_too_close_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(600), 0.001, GateConfig()) is None


def test_someone_at_the_edge_of_frame_is_rejected():
    raw = RawDetection(bbox=BBox(0, 90, 60, 390), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_missing_depth_rejects_rather_than_guesses():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, None, 0.001, GateConfig()) is None


def test_invalid_depth_rejects_rather_than_guesses():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(0), 0.001, GateConfig()) is None


def test_the_most_central_person_wins():
    centre = RawDetection(bbox=centred_box(), confidence=0.6)
    off_centre = RawDetection(bbox=BBox(180, 90, 280, 390), confidence=0.95)
    got = gate_detections([off_centre, centre], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.confidence == 0.6  # centrality beats confidence


def test_center_offset_is_signed_left_negative():
    left = RawDetection(bbox=BBox(200, 90, 300, 390), confidence=0.9)
    got = gate_detections([left], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.center_offset < 0


def test_a_box_reaching_outside_the_frame_is_clamped_not_crashed():
    raw = RawDetection(bbox=BBox(280, -50, 380, 700), confidence=0.9)
    got = gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None


def test_a_degenerate_box_is_rejected():
    raw = RawDetection(bbox=BBox(320, 240, 320, 240), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_no_detections_yields_none():
    assert gate_detections([], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


# ------------------------------------------------- everyone, for the interlock

def test_person_distances_reports_someone_too_close_to_greet():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    got = person_distances([raw], RGB_SHAPE, uniform_depth(600), 0.001, 0.5)
    assert got == [pytest.approx(0.6)]


def test_person_distances_reports_someone_at_the_edge_of_frame():
    raw = RawDetection(bbox=BBox(0, 90, 60, 390), confidence=0.9)
    got = person_distances([raw], RGB_SHAPE, uniform_depth(700), 0.001, 0.5)
    assert got == [pytest.approx(0.7)]


def test_person_distances_reports_an_unreadable_distance_as_none():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert person_distances([raw], RGB_SHAPE, uniform_depth(0), 0.001, 0.5) == [None]


def test_person_distances_skips_low_confidence_and_degenerate_boxes():
    faint = RawDetection(bbox=centred_box(), confidence=0.4)
    flat = RawDetection(bbox=BBox(320, 240, 320, 240), confidence=0.9)
    assert person_distances([faint, flat], RGB_SHAPE, uniform_depth(600), 0.001, 0.5) == []
