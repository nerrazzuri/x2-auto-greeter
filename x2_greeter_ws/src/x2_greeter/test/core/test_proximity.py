"""The arm's-reach interlock: everyone in view, not just the person greeted."""
import pytest

from x2_greeter.core.proximity import ProximityMonitor

FLOOR_M = 1.0
WINDOW_S = 0.5


@pytest.fixture
def monitor():
    return ProximityMonitor(floor_m=FLOOR_M, window_s=WINDOW_S)


def test_nothing_observed_yet_is_not_clear(monitor):
    assert monitor.clearance(10.0).clear is False


def test_an_empty_scene_is_clear(monitor):
    monitor.observe(10.0, [])
    assert monitor.clearance(10.1).clear is True


def test_everyone_beyond_the_floor_is_clear(monitor):
    monitor.observe(10.0, [1.6, 2.4])
    assert monitor.clearance(10.1).clear is True


def test_a_second_person_inside_the_floor_is_not_clear(monitor):
    """The case the old interlock missed: A gated at 1.6 m masked B at 0.7 m."""
    monitor.observe(10.0, [1.6, 0.7])
    got = monitor.clearance(10.1)
    assert got.clear is False
    assert '0.70' in got.reason


def test_a_person_whose_distance_cannot_be_read_is_not_clear(monitor):
    monitor.observe(10.0, [2.0, None])
    got = monitor.clearance(10.1)
    assert got.clear is False
    assert 'could not be read' in got.reason


def test_a_close_person_missed_by_one_frame_still_blocks_within_the_window(monitor):
    monitor.observe(10.0, [0.7])
    monitor.observe(10.2, [])
    assert monitor.clearance(10.3).clear is False


def test_a_close_person_older_than_the_window_no_longer_blocks(monitor):
    monitor.observe(10.0, [0.7])
    monitor.observe(10.6, [])
    assert monitor.clearance(10.7).clear is True


def test_a_stale_scene_is_not_clear(monitor):
    """No frame within the window means not knowing, which is not clear."""
    monitor.observe(10.0, [2.0])
    got = monitor.clearance(10.0 + WINDOW_S + 0.1)
    assert got.clear is False
    assert 'no depth frame' in got.reason


def test_exactly_at_the_floor_is_clear(monitor):
    monitor.observe(10.0, [FLOOR_M])
    assert monitor.clearance(10.0).clear is True
