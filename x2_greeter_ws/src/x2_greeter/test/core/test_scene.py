"""Who is in front of the robot, and which of them it is talking to.

The stature figures in these tests are geometry, not biometrics: the robot
never identifies anybody, it estimates how tall the thing in the box is and
uses that to decide whether to speak to a child the way you speak to a child.
"""
import math

import numpy as np
import pytest

from x2_greeter.core.types import BBox, RawDetection
from x2_greeter.core.scene import (
    AddressingMode, Person, SceneConfig, SceneSnapshot, child_mode,
    choose_subject, observe, stature_m)

VFOV = 1.1868   # 68 degrees, head RGB-D colour vertical FOV


def _person(distance_m, center_offset=0.0, stature=1.7, child=False,
            bbox=(0, 0, 10, 10), confidence=0.9):
    return Person(bbox=bbox, confidence=confidence, distance_m=distance_m,
                  center_offset=center_offset, stature_m=stature,
                  likely_child=child)


def test_stature_scales_with_distance_for_the_same_box():
    # The same pixel height twice as far away is twice as tall in the world.
    near = stature_m((0, 0, 100, 240), image_height=480, distance_m=2.0,
                     vertical_fov_rad=VFOV)
    far = stature_m((0, 0, 100, 240), image_height=480, distance_m=4.0,
                    vertical_fov_rad=VFOV)
    assert far == pytest.approx(2.0 * near, rel=1e-6)


def test_stature_of_a_full_height_box_is_the_full_vertical_fov_arc():
    expected = 2.0 * 3.0 * math.tan(VFOV / 2.0)
    assert stature_m((0, 0, 100, 480), 480, 3.0, VFOV) == pytest.approx(expected)


@pytest.mark.parametrize('image_height, distance_m', [(0, 3.0), (480, 0.0), (480, -1.0)])
def test_stature_is_unknown_rather_than_wrong_when_the_inputs_are_degenerate(
        image_height, distance_m):
    assert stature_m((0, 0, 10, 100), image_height, distance_m, VFOV) is None


def test_a_person_within_two_metres_makes_it_an_individual_conversation():
    people = [_person(1.4, center_offset=0.3), _person(3.0, center_offset=0.0)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.INDIVIDUAL
    assert subject.distance_m == 1.4, (
        'the subject is the most central person *inside* the individual '
        'range -- not the most central person overall, or the one further '
        'away is addressed as "you" while somebody stands at the robot\'s '
        'elbow being ignored')


def test_the_most_central_of_the_near_people_is_the_subject():
    people = [_person(1.9, center_offset=0.4), _person(1.2, center_offset=0.05)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.INDIVIDUAL
    assert subject.center_offset == 0.05


def test_everyone_beyond_two_metres_makes_it_a_group():
    people = [_person(2.5, center_offset=0.1), _person(3.4, center_offset=0.2)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.GROUP
    assert subject is not None, (
        'GROUP still needs somebody to look at; it changes how the robot '
        'speaks, not whether it has eyes')
    assert subject.center_offset == 0.1


def test_exactly_two_metres_counts_as_a_group():
    # The boundary is stated as "closer than 2.0 m" -- pinned so a later
    # refactor cannot quietly turn < into <=.
    subject, mode = choose_subject([_person(2.0)], individual_max_m=2.0)
    assert mode is AddressingMode.GROUP


def test_an_empty_scene_has_no_subject():
    subject, mode = choose_subject([], individual_max_m=2.0)
    assert subject is None
    assert mode is AddressingMode.GROUP


def test_child_mode_engages_when_either_signal_says_child():
    # Child mode only ever *restricts* what the robot says, so the safe error
    # is to engage it when unsure. Both signals are weak on their own: the
    # stature heuristic mistakes a crouching adult for a child, and the model
    # mistakes a short adult for one.
    assert child_mode(local_child=True, model_age_band=None) is True
    assert child_mode(local_child=False, model_age_band='child') is True
    assert child_mode(local_child=True, model_age_band='adult') is True
    assert child_mode(local_child=False, model_age_band='adult') is False
    assert child_mode(local_child=False, model_age_band=None) is False


@pytest.mark.parametrize('band', ['CHILD', ' child ', 'Child'])
def test_the_model_age_band_is_read_case_and_space_insensitively(band):
    assert child_mode(False, band) is True


def test_an_unrecognised_age_band_does_not_engage_child_mode():
    assert child_mode(False, 'teenager-ish') is False


def _frame_with(boxes, distances_m, shape=(480, 640, 3), depth_scale=0.001):
    """Build a depth array where each box reads back its intended distance."""
    depth = np.zeros(shape[:2], dtype=np.uint16)
    for (x1, y1, x2, y2), metres in zip(boxes, distances_m):
        depth[y1:y2, x1:x2] = int(metres / depth_scale)
    return depth


def test_observe_builds_one_person_per_passing_detection():
    boxes = [(100, 100, 200, 400), (400, 150, 480, 380)]
    depth = _frame_with(boxes, [1.5, 2.8])
    raws = [RawDetection(bbox=BBox(*box), confidence=0.9) for box in boxes]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=10.0)
    assert scene.person_count == 2
    assert scene.at_s == 10.0
    assert scene.closest_m == pytest.approx(1.5, abs=0.05)
    assert scene.mode is AddressingMode.INDIVIDUAL


def test_observe_drops_low_confidence_detections():
    boxes = [(100, 100, 200, 400)]
    depth = _frame_with(boxes, [1.5])
    raws = [RawDetection(bbox=BBox(*boxes[0]), confidence=0.3)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(confidence_min=0.5), at_s=0.0)
    assert scene.person_count == 0
    assert scene.subject is None


def test_observe_drops_a_detection_with_no_usable_depth():
    # An all-zero depth patch is "no reading", not "zero metres away". A
    # person admitted with a guessed distance would be gated for gestures
    # against a number nobody measured.
    depth = np.zeros((480, 640), dtype=np.uint16)
    raws = [RawDetection(bbox=BBox(100, 100, 200, 400), confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=0.0)
    assert scene.person_count == 0


def test_observe_drops_people_beyond_the_scene_horizon():
    boxes = [(100, 100, 200, 400)]
    depth = _frame_with(boxes, [8.0])
    raws = [RawDetection(bbox=BBox(*boxes[0]), confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(scene_max_m=5.0), at_s=0.0)
    assert scene.person_count == 0


def test_observe_flags_a_short_person_as_likely_child():
    # A 150 px box at 2 m in a 480 px frame is about 0.85 m tall.
    boxes = [(300, 200, 360, 350)]
    depth = _frame_with(boxes, [2.0])
    raws = [RawDetection(bbox=BBox(*boxes[0]), confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(child_stature_max_m=1.35), at_s=0.0)
    assert scene.person_count == 1
    assert scene.people[0].stature_m < 1.35
    assert scene.people[0].likely_child is True
    assert scene.has_child is True


def test_observe_does_not_flag_a_full_height_person_as_a_child():
    boxes = [(300, 40, 380, 440)]
    depth = _frame_with(boxes, [2.5])
    raws = [RawDetection(bbox=BBox(*boxes[0]), confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=0.0)
    assert scene.people[0].likely_child is False
    assert scene.has_child is False


def test_the_snapshot_is_frozen():
    scene = SceneSnapshot(people=(), subject=None, mode=AddressingMode.GROUP,
                          at_s=0.0)
    with pytest.raises(Exception):
        scene.at_s = 1.0
