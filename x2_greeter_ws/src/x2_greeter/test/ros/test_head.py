"""The head adapter: clamped twice, centred on every exit, off by default.

Imports of aimdk_msgs/x2_greeter.ros are deferred into functions rather than
done at module scope, matching every other file in test/ros/ -- aimdk_msgs
is only installed in the Docker harness, and a module-level import would
turn "deselected on host" into a collection error.

Corrected against the SDK's own joint_control.html and the JointStateArray/
JointState .msg files (not the task brief's prose): the head STATE topic
publishes JointStateArray (header, state: DomainErrorState, joints:
JointState[]), not JointCommandArray. JointState is a distinct type from
JointCommand -- it carries error_code, not stiffness/damping. The COMMAND
topic still carries JointCommandArray as documented.
"""
import pytest

pytestmark = pytest.mark.ros

from x2_greeter.core.gaze import (  # noqa: E402
    HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD)


class _FakePub:
    def __init__(self):
        self.messages = []
        self.destroyed = False

    def publish(self, msg):
        self.messages.append(msg)


class _FakeNode:
    def __init__(self):
        self.publisher = _FakePub()
        self.state_callback = None
        self.logs = []

    def create_publisher(self, msg_type, topic, qos, **kwargs):
        self.command_topic = topic
        self.command_type = msg_type
        return self.publisher

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.state_topic = topic
        self.state_type = msg_type
        self.state_callback = callback
        return object()

    def destroy_publisher(self, pub):
        pub.destroyed = True

    def destroy_subscription(self, sub):
        pass

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def _head_class():
    from x2_greeter.ros.head import Head
    return Head


def _yaw_of(msg):
    for entry in msg.joints:
        if entry.name == HEAD_YAW_JOINT:
            return entry.position
    raise AssertionError('no yaw entry in the command')


def _head(node=None, **kwargs):
    kwargs.setdefault('enabled', True)
    return _head_class()(node=node or _FakeNode(), **kwargs)


def _state_message(yaw):
    from aimdk_msgs.msg import JointState, JointStateArray

    msg = JointStateArray()
    entry = JointState()
    entry.name = HEAD_YAW_JOINT
    entry.position = yaw
    msg.joints = [entry]
    return msg


def test_a_disabled_head_publishes_nothing():
    node = _FakeNode()
    head = _head_class()(node=node, enabled=False)
    assert head.look_at(0.2) is False
    assert head.sweep() is False
    assert node.publisher.messages == [], (
        'head motion has never run on this robot under our code')


def test_it_publishes_to_the_documented_topics():
    from aimdk_msgs.msg import JointCommandArray, JointStateArray

    node = _FakeNode()
    _head(node)
    assert node.command_topic == '/aima/hal/joint/head/command'
    assert node.state_topic == '/aima/hal/joint/head/state'
    assert node.command_type is JointCommandArray
    assert node.state_type is JointStateArray, (
        'the state topic carries JointStateArray, not JointCommandArray -- '
        'see JointStateArray.msg / JointState.msg')


def test_a_command_carries_both_joints_in_the_vendor_order():
    node = _FakeNode()
    _head(node).look_at(0.1)
    names = [e.name for e in node.publisher.messages[0].joints]
    assert names == [HEAD_YAW_JOINT, HEAD_PITCH_JOINT]


def test_pitch_is_never_moved_in_this_phase():
    node = _FakeNode()
    _head(node).look_at(0.2)
    pitch = [e for e in node.publisher.messages[0].joints
             if e.name == HEAD_PITCH_JOINT][0]
    assert pitch.position == 0.0


@pytest.mark.parametrize('requested', [1.5, -1.5, 0.349, -0.349, 10.0])
def test_anything_beyond_our_limit_is_clamped_here_too(requested):
    node = _FakeNode()
    _head(node).look_at(requested)
    assert abs(_yaw_of(node.publisher.messages[0])) <= MAX_YAW_RAD + 1e-9


def test_the_second_clamp_is_not_theatre():
    # core.gaze clamps, and so does this. The day somebody calls look_at
    # with a number that did not pass through gaze.py, this is the only
    # clamp that runs.
    node = _FakeNode()
    _head(node).look_at(0.348)          # inside the vendor limit, past ours
    assert abs(_yaw_of(node.publisher.messages[0])) == pytest.approx(MAX_YAW_RAD)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_a_non_finite_yaw_becomes_centre_not_a_motor_command(bad):
    node = _FakeNode()
    _head(node).look_at(bad)
    assert _yaw_of(node.publisher.messages[0]) == 0.0


def test_current_yaw_tracks_what_was_commanded():
    head = _head()
    head.look_at(0.1)
    assert head.current_yaw == pytest.approx(0.1)


def test_centre_returns_to_zero():
    node = _FakeNode()
    head = _head(node)
    head.look_at(0.2)
    head.centre()
    assert _yaw_of(node.publisher.messages[-1]) == 0.0
    assert head.current_yaw == 0.0


def test_a_sweep_starts_and_ends_at_centre():
    node = _FakeNode()
    _head(node).sweep(sleep=lambda s: None)
    yaws = [_yaw_of(m) for m in node.publisher.messages]
    assert yaws[0] == 0.0
    assert yaws[-1] == 0.0


def test_a_sweep_visits_both_extremes_and_never_exceeds_the_limit():
    node = _FakeNode()
    _head(node).sweep(sleep=lambda s: None)
    yaws = [_yaw_of(m) for m in node.publisher.messages]
    assert min(yaws) == pytest.approx(-MAX_YAW_RAD)
    assert max(yaws) == pytest.approx(MAX_YAW_RAD)
    assert all(abs(y) <= MAX_YAW_RAD + 1e-9 for y in yaws)


def test_the_sweep_asks_for_a_capture_at_each_hold():
    captures = []
    _head().sweep(on_capture=lambda yaw: captures.append(yaw),
                  sleep=lambda s: None)
    assert len(captures) == 2, 'one capture at each end, not one per sample'
    assert captures[0] == pytest.approx(-MAX_YAW_RAD)
    assert captures[1] == pytest.approx(MAX_YAW_RAD)


def test_a_capture_callback_that_raises_still_leaves_the_head_centred():
    node = _FakeNode()
    head = _head(node)

    def _boom(yaw):
        raise RuntimeError('the camera exploded')

    with pytest.raises(RuntimeError):
        head.sweep(on_capture=_boom, sleep=lambda s: None)
    assert _yaw_of(node.publisher.messages[-1]) == 0.0, (
        'a head left turned 15 degrees off-centre is how the next session '
        'starts looking at a wall')
    assert head.current_yaw == 0.0


def test_destroy_centres_before_releasing_the_publisher():
    node = _FakeNode()
    head = _head(node)
    head.look_at(0.25)
    head.destroy()
    assert _yaw_of(node.publisher.messages[-1]) == 0.0
    assert node.publisher.destroyed is True


def test_state_feedback_is_recorded_when_it_arrives():
    node = _FakeNode()
    head = _head(node)
    assert node.state_callback is not None, (
        'the state topic is TRANSIENT_LOCAL, so the last value is there '
        'the moment we subscribe')
    head._on_state(_state_message(0.12))
    assert head.measured_yaw == pytest.approx(0.12)
