import threading

import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def rig(ros):
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=False)
    caller = ros.create_node('motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot, caller
    executor.shutdown()
    caller.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def gesture_dispatcher(caller):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.gesture import GestureDispatcher

    return GestureDispatcher(caller, callback_group=ReentrantCallbackGroup())


def mode_guard(caller, **kwargs):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.mode_guard import ModeGuard

    kwargs.setdefault('callback_group', ReentrantCallbackGroup())
    return ModeGuard(caller, **kwargs)


def test_it_performs_the_requested_motion_on_the_requested_area(rig):
    robot, caller = rig
    assert gesture_dispatcher(caller).perform(1002, 2) is True
    assert robot.motion_requests[0].motion.value == 1002
    assert robot.motion_requests[0].area.value == 2


def test_it_does_not_interrupt_a_motion_already_running(rig):
    robot, caller = rig
    gesture_dispatcher(caller).perform(1013, 1)
    assert robot.motion_requests[0].interrupt is False


def test_it_stamps_the_request_header(rig):
    robot, caller = rig
    gesture_dispatcher(caller).perform(3001, 11)
    stamp = robot.motion_requests[0].header.stamp
    assert stamp.sec > 0 or stamp.nanosec > 0


def test_it_reports_failure_when_the_service_is_absent(ros):
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert gesture_dispatcher(caller).perform(1002, 2) is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)


def test_it_accepts_a_motion_that_is_already_running(ros):
    """state == RUNNING counts as accepted, regardless of header.code.

    CommonTaskResponse's real answer lives in `state`, not `header.code`, so
    this pins state as the field that is actually checked. FakeRobot always
    reports SUCCESS, so this branch needs its own test-local responder (the
    same pattern as RejectingAudioServer in test_speech.py) rather than
    modifying FakeRobot, which Task 15 depends on.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetMcPresetMotion
    from x2_greeter.ros.gesture import PRESET_MOTION_SERVICE

    class RunningMotionServer(Node):
        def __init__(self):
            super().__init__('running_motion_server')
            self.create_service(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                self._on_preset_motion)

        def _on_preset_motion(self, request, response):
            response.response.header.code = 1
            response.response.state.value = CommonState.RUNNING
            response.response.task_id = 7
            return response

    server = RunningMotionServer()
    caller = ros.create_node('running_motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert gesture_dispatcher(caller).perform(1002, 2) is True
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_it_rejects_a_motion_the_robot_refuses(ros):
    """A non-zero code with a state other than RUNNING is a genuine rejection."""
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetMcPresetMotion
    from x2_greeter.ros.gesture import PRESET_MOTION_SERVICE

    class RejectingMotionServer(Node):
        def __init__(self):
            super().__init__('rejecting_motion_server')
            self.create_service(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                self._on_preset_motion)

        def _on_preset_motion(self, request, response):
            response.response.header.code = 1
            response.response.state.value = CommonState.FAILURE
            response.response.task_id = 9
            return response

    server = RejectingMotionServer()
    caller = ros.create_node('rejecting_motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert gesture_dispatcher(caller).perform(1002, 2) is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_it_rejects_when_the_header_is_clean_but_state_reports_failure(ros):
    """The exact case the header-first check gets wrong.

    header.code is default-zero, so a controller that refuses the motion only
    through `state` and never touches the header must still be read as a
    rejection -- not as accepted because the header happens to be clean. This
    is the message's own shape inviting the bug: `state` is the field
    CommonTaskResponse exists to carry.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetMcPresetMotion
    from x2_greeter.ros.gesture import PRESET_MOTION_SERVICE

    class HeaderCleanFailureServer(Node):
        def __init__(self):
            super().__init__('header_clean_failure_motion_server')
            self.create_service(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                self._on_preset_motion)

        def _on_preset_motion(self, request, response):
            # header.code left at its default 0; state carries the real,
            # refusing answer.
            response.response.state.value = CommonState.FAILURE
            response.response.task_id = 13
            return response

    server = HeaderCleanFailureServer()
    caller = ros.create_node('header_clean_failure_motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert gesture_dispatcher(caller).perform(1002, 2) is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_gesturing_is_allowed_in_stand_default(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.STAND_DEFAULT
    guard = mode_guard(caller)
    assert guard.gesturing_allowed() is True
    assert guard.last_action == McAction.STAND_DEFAULT


def test_gesturing_is_refused_in_any_other_mode(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    for mode in (McAction.PASSIVE_DEFAULT, McAction.JOINT_DEFAULT,
                 McAction.DAMPING_DEFAULT, McAction.LOCOMOTION_DEFAULT):
        robot.current_action = mode
        assert mode_guard(caller).gesturing_allowed() is False


def test_a_repeated_refusal_still_asks_the_robot_every_time(rig):
    """The warning is logged once, but the mode is never assumed from a cache."""
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.JOINT_DEFAULT
    guard = mode_guard(caller)
    for _ in range(5):
        assert guard.gesturing_allowed() is False
    assert robot.mode_queries >= 5


def test_an_unreachable_mode_service_refuses_rather_than_assumes(ros):
    """Not knowing the mode is not the same as knowing it is safe."""
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_guard_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert mode_guard(caller).gesturing_allowed() is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)


def test_the_guard_can_be_switched_off_for_bench_testing(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.PASSIVE_DEFAULT
    guard = mode_guard(caller, require_stand_default=False)
    assert guard.gesturing_allowed() is True
    assert robot.mode_queries == 0    # it does not even ask


def test_the_guard_never_changes_the_mode(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.JOINT_DEFAULT
    mode_guard(caller).gesturing_allowed()
    assert robot.current_action == McAction.JOINT_DEFAULT
