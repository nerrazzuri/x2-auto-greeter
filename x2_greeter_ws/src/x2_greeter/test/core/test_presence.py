import pytest

from x2_greeter.core.presence import PresenceConfig, PresenceState, PresenceTracker
from x2_greeter.core.types import BBox, Detection

CFG = PresenceConfig(dwell_s=1.0, loss_grace_s=0.5, clear_s=3.0,
                     cooldown_s=30.0, reject_cooldown_s=5.0, confirm_timeout_s=10.0)


def person(distance_m=2.0, offset=0.0):
    return Detection(bbox=BBox(270, 90, 370, 390), confidence=0.9,
                     distance_m=distance_m, center_offset=offset)


@pytest.fixture
def tracker():
    return PresenceTracker(CFG)


def drive_to_confirming(tracker, t0=0.0):
    """Standard path: someone appears and stays put for the dwell period."""
    tracker.update(t0, person())
    return tracker.update(t0 + CFG.dwell_s, person())


def test_starts_idle(tracker):
    assert tracker.state is PresenceState.IDLE


def test_a_detection_moves_to_candidate(tracker):
    assert tracker.update(0.0, person()) is None
    assert tracker.state is PresenceState.CANDIDATE


def test_dwelling_moves_to_confirming_and_returns_the_detection(tracker):
    tracker.update(0.0, person())
    assert tracker.update(0.9, person()) is None
    assert tracker.state is PresenceState.CANDIDATE
    returned = tracker.update(1.0, person(distance_m=2.4))
    assert tracker.state is PresenceState.CONFIRMING
    assert returned is not None
    assert returned.distance_m == pytest.approx(2.4)


def test_the_confirm_detection_is_returned_only_once(tracker):
    drive_to_confirming(tracker)
    assert tracker.update(1.5, person()) is None


def test_a_passer_by_never_reaches_confirming(tracker):
    tracker.update(0.0, person())
    tracker.update(0.4, person())
    tracker.update(0.6, None)
    assert tracker.update(1.2, None) is None
    assert tracker.state is PresenceState.IDLE


def test_a_brief_detection_dropout_does_not_reset_the_dwell(tracker):
    tracker.update(0.0, person())
    tracker.update(0.4, None)          # inside the 0.5 s grace
    tracker.update(0.6, person())
    tracker.update(1.0, person())
    assert tracker.state is PresenceState.CONFIRMING


def test_candidate_returns_to_idle_after_the_loss_grace(tracker):
    tracker.update(0.0, person())
    tracker.update(0.2, None)
    tracker.update(0.71, None)
    assert tracker.state is PresenceState.IDLE


def test_a_positive_verdict_greets(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    assert tracker.state is PresenceState.GREETING


def test_a_negative_verdict_enters_the_short_cooldown(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=False)
    assert tracker.state is PresenceState.COOLDOWN
    # The short reject cooldown expires long before the 30 s greeting cooldown.
    tracker.update(2.0, None)
    tracker.update(6.3, None)
    assert tracker.state is PresenceState.IDLE


def test_dispatching_a_greeting_enters_the_long_cooldown(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    assert tracker.state is PresenceState.COOLDOWN


def test_the_cooldown_does_not_lift_while_the_person_is_still_there(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    for t in (10.0, 20.0, 31.0, 60.0, 120.0):
        tracker.update(t, person())
    assert tracker.state is PresenceState.COOLDOWN


def test_the_cooldown_lifts_after_they_leave(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    tracker.update(40.0, person())     # still there, cooldown already elapsed
    assert tracker.state is PresenceState.COOLDOWN
    tracker.update(41.0, None)         # they walk away
    tracker.update(43.0, None)
    assert tracker.state is PresenceState.COOLDOWN   # clear_s not yet satisfied
    tracker.update(44.1, None)
    assert tracker.state is PresenceState.IDLE


def test_someone_returning_during_the_clear_window_restarts_it(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    tracker.update(40.0, None)
    tracker.update(42.0, person())     # came back, resets the clear timer
    tracker.update(43.0, None)
    tracker.update(45.0, None)
    assert tracker.state is PresenceState.COOLDOWN
    tracker.update(46.1, None)
    assert tracker.state is PresenceState.IDLE


def test_a_second_person_can_be_greeted_after_the_cycle_completes(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    for t in (40.0, 41.0, 42.0, 43.0, 44.1):
        tracker.update(t, None)
    assert tracker.state is PresenceState.IDLE
    assert drive_to_confirming(tracker, t0=50.0) is not None


def test_has_live_detection_is_true_inside_the_grace_window(tracker):
    drive_to_confirming(tracker)
    tracker.update(1.2, person())
    assert tracker.has_live_detection is True


def test_has_live_detection_goes_false_after_the_grace_window(tracker):
    drive_to_confirming(tracker)
    tracker.update(1.2, person())
    tracker.update(2.0, None)
    assert tracker.has_live_detection is False


def test_has_live_detection_is_false_before_anything_is_seen(tracker):
    assert tracker.has_live_detection is False


def test_a_lost_verdict_does_not_wedge_the_machine(tracker):
    drive_to_confirming(tracker)
    tracker.update(5.0, person())
    tracker.update(11.5, person())
    assert tracker.state is PresenceState.IDLE


def test_a_verdict_arriving_after_the_confirm_timeout_is_ignored(tracker):
    drive_to_confirming(tracker)
    tracker.update(11.5, None)
    assert tracker.state is PresenceState.IDLE
    tracker.on_verdict(12.0, person_present=True)
    assert tracker.state is PresenceState.IDLE


def test_a_verdict_outside_confirming_is_ignored(tracker):
    tracker.on_verdict(0.5, person_present=True)
    assert tracker.state is PresenceState.IDLE


def test_dispatch_outside_greeting_is_ignored(tracker):
    tracker.on_greeting_dispatched(0.5)
    assert tracker.state is PresenceState.IDLE
