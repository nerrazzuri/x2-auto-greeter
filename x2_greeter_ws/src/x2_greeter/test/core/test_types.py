import dataclasses

import pytest

from x2_greeter.core.types import BBox, Detection, JpegFrame, RawDetection, SceneContext, Verdict


def test_bbox_geometry():
    box = BBox(x1=10, y1=20, x2=110, y2=220)
    assert box.width == 100
    assert box.height == 200
    assert box.cx == 60.0
    assert box.cy == 120.0


def test_bbox_is_frozen():
    box = BBox(0, 0, 1, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        box.x1 = 5


def test_detection_carries_gating_results():
    det = Detection(bbox=BBox(0, 0, 10, 10), confidence=0.9,
                    distance_m=2.0, center_offset=-0.1)
    assert det.distance_m == 2.0
    assert det.center_offset == pytest.approx(-0.1)


def test_raw_detection_holds_only_what_the_detector_knows():
    raw = RawDetection(bbox=BBox(0, 0, 10, 10), confidence=0.75)
    assert {f.name for f in dataclasses.fields(raw)} == {'bbox', 'confidence'}


def test_scene_context_describes_where_the_person_is():
    ctx = SceneContext(distance_m=1.8, center_offset=0.05)
    assert ctx.distance_m == 1.8


def test_verdict_defaults_gesture_to_none():
    v = Verdict(person_present=True, facing_robot=False, confidence=0.8,
                greeting='Hello!', reason='saw a person', source='canned')
    assert v.gesture is None


def test_jpeg_frame_defaults_media_type():
    frame = JpegFrame(data=b'\xff\xd8\xff')
    assert frame.media_type == 'image/jpeg'
