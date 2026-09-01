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
