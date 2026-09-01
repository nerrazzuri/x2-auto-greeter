"""The whole thing, on a simulated robot.

Camera frames go in; a TTS request and a preset motion come out, in the right
order, with the right payloads.
"""
import threading
import time

import pytest

pytestmark = pytest.mark.ros

FAST_PARAMS = [
    ('detect.detector', 'scripted'),
    ('backend.provider', 'canned'),
    ('presence.dwell_s', 0.3),
    ('presence.loss_grace_s', 0.5),
    ('presence.clear_s', 0.5),
    ('presence.cooldown_s', 1.0),
    ('presence.reject_cooldown_s', 0.5),
    ('speech.audio_file_count', 6),
]


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


def build_rig(ros, robot_kwargs=None, extra_params=()):
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.parameter import Parameter

    from x2_greeter.core.detectors import ScriptedDetector
    from x2_greeter.core.types import BBox, RawDetection
    from x2_greeter.ros.greeting_node import GreetingNode
    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=True, **(robot_kwargs or {}))

    overrides = [Parameter(name, value=value)
                 for name, value in list(FAST_PARAMS) + list(extra_params)]
    node = GreetingNode(parameter_overrides=overrides)

    node.detector = ScriptedDetector(
        [RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])

    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return robot, node, executor, thread


def teardown_rig(robot, node, executor, thread):
    executor.shutdown()
    node.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def wait_until(predicate, timeout_s=25.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_person_in_front_gets_greeted_and_gestured_at(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: robot.tts_requests and robot.motion_requests), \
            'the robot never greeted'

        spoken = robot.tts_requests[0].tts_req
        assert spoken.text.strip()
        assert spoken.domain == 'x2_greeter'
        assert spoken.priority_level.value == 6

        from x2_greeter.core.gestures import CATALOGUE
        motion = robot.motion_requests[0]
        spec = next(s for s in CATALOGUE.values() if s.motion_id == motion.motion.value)
        assert spec.name in node.selector.enabled_names
        assert motion.area.value in spec.areas
        assert motion.interrupt is False
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_mode_is_checked_before_the_robot_moves(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: robot.motion_requests)
        assert robot.mode_queries >= 1
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_robot_speaks_but_does_not_gesture_outside_stand_default(ros):
    from aimdk_msgs.msg import McAction

    robot, node, executor, thread = build_rig(
        ros, robot_kwargs={'current_action': McAction.JOINT_DEFAULT})
    try:
        assert wait_until(lambda: bool(robot.tts_requests)), 'the robot never spoke'
        time.sleep(2.0)
        assert robot.motion_requests == []
        assert robot.current_action == McAction.JOINT_DEFAULT   # never changed
    finally:
        teardown_rig(robot, node, executor, thread)


def test_it_does_not_greet_the_same_person_over_and_over(ros):
    """The person never leaves, so the cooldown never lifts."""
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        time.sleep(4.0)     # four times the 1.0 s cooldown
        assert len(robot.tts_requests) == 1
    finally:
        teardown_rig(robot, node, executor, thread)


def test_someone_leaving_and_returning_is_greeted_again(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        node.detector.set_detections([])                       # they walk away
        time.sleep(2.5)                                        # cooldown + clear
        from x2_greeter.core.types import BBox, RawDetection
        node.detector.set_detections(
            [RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])
        assert wait_until(lambda: len(robot.tts_requests) >= 2), 'never greeted again'
    finally:
        teardown_rig(robot, node, executor, thread)


def test_someone_too_far_away_is_not_greeted(ros):
    robot, node, executor, thread = build_rig(ros, robot_kwargs={'distance_mm': 5000})
    try:
        time.sleep(4.0)
        assert robot.tts_requests == []
        assert robot.motion_requests == []
    finally:
        teardown_rig(robot, node, executor, thread)


def test_a_failing_tts_falls_back_to_an_audio_file(ros):
    robot, node, executor, thread = build_rig(ros, robot_kwargs={'tts_succeeds': False})
    try:
        assert wait_until(lambda: bool(robot.audio_requests)), 'never played a recording'
        assert node.speech.demoted is True
        assert robot.audio_requests[0].file.info.sample_rate == 16000
    finally:
        teardown_rig(robot, node, executor, thread)


def test_no_image_reaches_the_robot_or_the_disk(ros):
    """The canned path must not encode or transmit a frame at all."""
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        assert node._needs_frame is False
        for request in robot.tts_requests:
            assert 'jpeg' not in request.tts_req.text.lower()
    finally:
        teardown_rig(robot, node, executor, thread)


# --------------------------------------------------------------------------
# Addendum tests (from the Task 15 review): the failure paths a bug would be
# quietest and most expensive in.
# --------------------------------------------------------------------------


def test_a_crashed_greeting_still_lets_the_next_person_be_greeted(ros):
    """Test A: PresenceTracker must leave GREETING even when the worker raises.

    Before the fix, an exception inside the greeting worker left the tracker
    stuck in GREETING forever: one ERROR line, then a robot that never greets
    again. The fix releases the tracker in a finally. This is what keeps it
    released.

    Assert on greetings_dispatched (a crashed attempt must not count as a
    successful greeting) and on the tracker leaving COOLDOWN so a second,
    genuine greeting can happen.
    """
    from x2_greeter.core.presence import PresenceState
    from x2_greeter.core.types import BBox, RawDetection

    robot, node, executor, thread = build_rig(ros)

    calls = []
    original_allowed = node.mode_guard.gesturing_allowed

    def boom_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('injected gesture-path failure')
        return original_allowed()

    node.mode_guard.gesturing_allowed = boom_once
    try:
        assert wait_until(lambda: bool(calls)), 'the gesture path was never reached'
        assert node.wait_for_idle_worker(10.0), 'the greeting worker never finished'
        # The crashed attempt must not count as a successful greeting, and it
        # must not have left the tracker wedged in GREETING.
        assert node.greetings_dispatched == 0
        assert node.tracker.state is not PresenceState.GREETING

        # Same drive as test_someone_leaving_and_returning_is_greeted_again: the
        # person leaves, the cooldown lifts, they come back.
        node.detector.set_detections([])
        time.sleep(2.5)
        node.detector.set_detections(
            [RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])
        assert wait_until(lambda: node.greetings_dispatched >= 1), \
            'the robot never greeted again after the crash'
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_robot_still_speaks_when_the_mode_service_never_answers(ros):
    """Test B1: ModeGuard refuses when it cannot reach the service at all.

    'Not knowing is not knowing it is safe' — an unreachable GetMcAction
    service must not stop the greeting from being spoken.
    """
    from aimdk_msgs.srv import GetMcAction

    robot, node, executor, thread = build_rig(ros)
    try:
        # Point the mode guard's client at a service nobody serves, so every
        # call_with_retry attempt is dropped and gesturing_allowed() refuses.
        node.mode_guard._client = node.create_client(
            GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction_does_not_exist',
            callback_group=node._callback_group)

        assert wait_until(lambda: bool(robot.tts_requests)), 'the robot never spoke'
        assert node.wait_for_idle_worker(10.0)
        assert robot.motion_requests == []
        spoken = robot.tts_requests[0].tts_req
        assert spoken.text.strip()
    finally:
        teardown_rig(robot, node, executor, thread)


def test_no_set_mode_call_is_ever_made(ros):
    """Test B2: the safety case rests on the node never changing motion mode.

    Asserted against the node's own client graph rather than by inspection,
    so a future refactor that adds an 'auto-stand' convenience trips this
    test rather than a robot.
    """
    from aimdk_msgs.msg import McAction

    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: robot.tts_requests and robot.motion_requests), \
            'a full greeting cycle never completed'
        assert node.wait_for_idle_worker(10.0)

        client_names = {name for name, _types in node.get_client_names_and_types_by_node(
            node.get_name(), node.get_namespace())}
        offenders = [name for name in client_names if 'SetMcAction' in name]
        assert offenders == [], f'the node holds a mode-setting client: {offenders}'
        assert robot.current_action == McAction.STAND_DEFAULT   # never changed
    finally:
        teardown_rig(robot, node, executor, thread)


def test_a_slow_detector_does_not_corrupt_the_greeting(ros):
    """Test C: the perception path must not re-enter detect() on itself.

    Before the fix, every camera subscription shared a ReentrantCallbackGroup
    under a MultiThreadedExecutor, so a slow detect() could run concurrently
    with itself, putting two frames into one shared detector at once. The fix
    moves perception to its own MutuallyExclusiveCallbackGroup.

    A continuously-present person must still get exactly one greeting, and
    speech must still succeed — i.e. the slow perception loop must not starve
    the executor of the threads that deliver service responses.
    """
    robot, node, executor, thread = build_rig(ros)

    lock = threading.Lock()
    state = {'concurrent': 0, 'max_concurrent': 0}
    inner = node.detector

    class SlowDetector:
        def detect(self, bgr):
            with lock:
                state['concurrent'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['concurrent'])
            try:
                time.sleep(0.4)   # well over the fake robot's 0.1 s frame period
                return inner.detect(bgr)
            finally:
                with lock:
                    state['concurrent'] -= 1

    node.detector = SlowDetector()
    try:
        assert wait_until(lambda: bool(robot.tts_requests)), 'the robot never spoke'
        assert node.wait_for_idle_worker(15.0)
        time.sleep(2.0)   # let several more would-be-overlapping frames go by
        assert state['max_concurrent'] <= 1, \
            'detect() re-entered itself: frames overlapped'
        assert len(robot.tts_requests) == 1
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_canned_backend_never_receives_a_frame(ros):
    """Test D (canned half): _needs_frame is the only switch to the network."""
    robot, node, executor, thread = build_rig(ros)

    received = []
    original_compose = node.policy.compose

    def spying_compose(frame, ctx):
        received.append(frame)
        return original_compose(frame, ctx)

    node.policy.compose = spying_compose
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        assert node.wait_for_idle_worker(10.0)
        assert received, 'the backend was never asked'
        assert received[0] is None
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_claude_backend_receives_a_non_empty_jpeg_frame(ros, monkeypatch):
    """Test D (claude half): the cloud provider is the only one shown a frame.

    A dummy key is set only so _build_backends takes the claude branch and
    constructs a real ClaudeBackend; node.policy is replaced with a stub
    before any frame arrives, so no network call is ever made.
    """
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-dummy-not-a-real-key')

    from x2_greeter.core.types import JpegFrame, Verdict

    robot, node, executor, thread = build_rig(
        ros, extra_params=[('backend.provider', 'claude')])
    try:
        assert node._primary_name == 'claude', \
            'test setup did not actually select the claude backend'

        received = []

        class StubPolicy:
            def compose(self, frame, ctx):
                received.append(frame)
                return Verdict(person_present=True, facing_robot=False, confidence=1.0,
                               greeting='Hello there, from the stub.', reason='stub',
                               source='claude', gesture=None)

        node.policy = StubPolicy()

        assert wait_until(lambda: bool(robot.tts_requests))
        assert node.wait_for_idle_worker(10.0)
        assert received, 'the backend was never asked'
        frame = received[0]
        assert isinstance(frame, JpegFrame)
        assert len(frame.data) > 0
    finally:
        teardown_rig(robot, node, executor, thread)
