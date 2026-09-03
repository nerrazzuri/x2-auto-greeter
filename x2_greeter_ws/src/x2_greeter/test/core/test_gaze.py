"""Head yaw geometry: the clamp is the safety property, the rest is framing.

The vendor documents +/-20 degrees of head yaw and says pitch is unavailable.
We clamp to +/-15 so that an arithmetic error, a bad FOV constant or a
malformed bbox cannot walk the joint into its own hard stop. Every test here
exists to keep that margin, or to keep the sweep from being mistaken for a
way to see a wider scene.
"""
import pytest

from x2_greeter.core.gaze import (
    HEAD_HFOV_RAD, HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD,
    VENDOR_LIMIT_RAD, clamp_yaw, group_drift, sweep_waypoints, yaw_for)


def test_our_clamp_sits_inside_the_vendor_limit():
    assert MAX_YAW_RAD == pytest.approx(0.262, abs=1e-3)       # 15 degrees
    assert VENDOR_LIMIT_RAD == pytest.approx(0.349, abs=1e-3)  # 20 degrees
    assert MAX_YAW_RAD < VENDOR_LIMIT_RAD, (
        'the whole point of the clamp is the margin: if it ever reaches the '
        'vendor limit, a rounding error is enough to hit the hard stop')


def test_the_joint_names_are_the_vendor_names():
    assert HEAD_YAW_JOINT == 'head_yaw'
    assert HEAD_PITCH_JOINT == 'head_pitch'


@pytest.mark.parametrize('raw, expected', [
    (0.0, 0.0), (0.1, 0.1), (-0.1, -0.1),
    (10.0, MAX_YAW_RAD), (-10.0, -MAX_YAW_RAD),
    (MAX_YAW_RAD, MAX_YAW_RAD), (-MAX_YAW_RAD, -MAX_YAW_RAD),
])
def test_clamp_yaw_never_returns_more_than_the_limit(raw, expected):
    assert clamp_yaw(raw) == pytest.approx(expected)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_clamp_yaw_turns_a_non_finite_value_into_centre(bad):
    # A NaN yaw sent to the controller is not a small command, it is an
    # undefined one. Centre is the only safe reading of "I do not know".
    assert clamp_yaw(bad) == 0.0


def test_a_centred_person_needs_no_yaw():
    assert yaw_for(320.0, 640) == pytest.approx(0.0)


def test_a_person_at_the_edge_of_frame_yaws_towards_half_the_fov():
    # Full deflection would be hfov/2 = 0.820 rad, which the clamp cuts to
    # MAX_YAW_RAD -- the clamp binds long before the frame edge does.
    assert yaw_for(640.0, 640) == pytest.approx(MAX_YAW_RAD)
    assert yaw_for(0.0, 640) == pytest.approx(-MAX_YAW_RAD)


def test_a_person_slightly_off_centre_yaws_proportionally():
    # 25% right of centre -> 0.25 * hfov/2 = 0.205 rad, inside the clamp.
    expected = 0.25 * HEAD_HFOV_RAD / 2.0
    assert expected < MAX_YAW_RAD
    assert yaw_for(400.0, 640) == pytest.approx(expected, abs=1e-4)


def test_the_yaw_sign_is_configurable_because_nobody_has_measured_it_yet():
    # Whether +yaw turns the head left or right is an open hardware question
    # (spec section 18). It is a config value with a default, never a guess
    # baked into the arithmetic.
    assert yaw_for(400.0, 640, yaw_sign=-1) == pytest.approx(
        -yaw_for(400.0, 640, yaw_sign=1))


@pytest.mark.parametrize('width', [0, -640])
def test_a_degenerate_image_width_yields_centre(width):
    assert yaw_for(100.0, width) == 0.0


def test_the_sweep_starts_and_ends_at_centre():
    steps = sweep_waypoints()
    assert steps[0].yaw == pytest.approx(0.0)
    assert steps[-1].yaw == pytest.approx(0.0)


def test_every_sweep_waypoint_is_inside_the_clamp():
    for step in sweep_waypoints():
        assert abs(step.yaw) <= MAX_YAW_RAD + 1e-9, step


def test_the_sweep_visits_both_extremes():
    yaws = [step.yaw for step in sweep_waypoints()]
    assert min(yaws) == pytest.approx(-MAX_YAW_RAD)
    assert max(yaws) == pytest.approx(MAX_YAW_RAD)


def test_the_sweep_captures_exactly_twice_and_only_while_held():
    captures = [step for step in sweep_waypoints() if step.capture]
    assert len(captures) == 2, (
        'two off-axis frames, one per side: a capture per waypoint would '
        'mean a dozen head-camera frames per sweep for no extra information')
    assert {round(step.yaw, 3) for step in captures} == {
        round(-MAX_YAW_RAD, 3), round(MAX_YAW_RAD, 3)}


def test_the_sweep_timestamps_increase_at_the_command_rate():
    steps = sweep_waypoints(rate_hz=20.0)
    deltas = [b.t_s - a.t_s for a, b in zip(steps, steps[1:])]
    assert all(delta == pytest.approx(0.05, abs=1e-6) for delta in deltas)


def test_a_faster_command_rate_produces_more_waypoints_over_the_same_span():
    slow = sweep_waypoints(rate_hz=10.0)
    fast = sweep_waypoints(rate_hz=20.0)
    assert len(fast) > len(slow)
    assert fast[-1].t_s == pytest.approx(slow[-1].t_s, abs=0.11)


def test_the_group_drift_is_a_slow_bounded_wander():
    for t_s in [0.0, 1.0, 3.0, 6.0, 9.0, 12.0, 100.0]:
        assert abs(group_drift(t_s)) <= MAX_YAW_RAD + 1e-9
    assert group_drift(0.0) == pytest.approx(0.0)
    assert group_drift(3.0, period_s=12.0) > 0.0
    assert group_drift(9.0, period_s=12.0) < 0.0


def test_the_group_drift_is_a_pure_function_of_time():
    assert group_drift(4.2) == group_drift(4.2)
