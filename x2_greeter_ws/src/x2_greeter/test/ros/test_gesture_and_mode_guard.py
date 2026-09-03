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


@pytest.mark.parametrize('state_name', ['SUCCESS', 'PENDING', 'CREATED'])
def test_it_accepts_every_documented_acceptance_state(ros, state_name):
    """_ACCEPTED_STATES lists SUCCESS, PENDING and CREATED besides RUNNING
    (which has its own test above); pin all three so the whole accepted set
    is exercised, not just the one FakeRobot happens to return.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetMcPresetMotion
    from x2_greeter.ros.gesture import PRESET_MOTION_SERVICE

    state_value = getattr(CommonState, state_name)

    class AcceptedStateServer(Node):
        def __init__(self):
            super().__init__(f'accepted_state_{state_name.lower()}_server')
            self.create_service(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                self._on_preset_motion)

        def _on_preset_motion(self, request, response):
            response.response.header.code = 0
            response.response.state.value = state_value
            response.response.task_id = 42
            return response

    server = AcceptedStateServer()
    caller = ros.create_node(f'accepted_state_{state_name.lower()}_caller')
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
    """A state outside _ACCEPTED_STATES is a genuine rejection, regardless of
    header.code -- state is what decides, not the header.
    """
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


def test_it_accepts_when_only_the_header_reports_it(ros):
    """A controller that fills only the header is still understood as accepted.

    state.value is left at its default (UNKNOWN, 0) and header.code is left
    at its default (0) -- the mirror image of
    test_it_rejects_when_the_header_is_clean_but_state_reports_failure. A
    controller that speaks only through the header, and cleanly, is the best
    signal available from it, and must not be misread as a rejection.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.srv import SetMcPresetMotion
    from x2_greeter.ros.gesture import PRESET_MOTION_SERVICE

    class HeaderOnlyAcceptedServer(Node):
        def __init__(self):
            super().__init__('header_only_accepted_motion_server')
            self.create_service(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                self._on_preset_motion)

        def _on_preset_motion(self, request, response):
            # response.response.state.value left at its default (UNKNOWN);
            # response.response.header.code left at its default (0).
            response.response.task_id = 21
            return response

    server = HeaderOnlyAcceptedServer()
    caller = ros.create_node('header_only_accepted_motion_caller')
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


# ------------------------- the controller that names the mode but not its id

def test_an_unset_action_id_with_a_stand_default_description_is_accepted(rig):
    """Found on hardware: the X2 controller answers GetMcAction with
    current_action.value == 0 and action_desc == 'STAND_DEFAULT'. Zero is not
    a mode -- McAction's enum starts at 1 -- so it means "not populated", and
    the description is the only signal there is. Reading only the id made the
    guard refuse forever on a robot that was, in fact, standing.
    """
    from aimdk_msgs.msg import McActionStatus

    robot, caller = rig
    robot.current_action = 0
    robot.action_desc = 'STAND_DEFAULT'
    robot.action_status = McActionStatus.RUNNING

    assert mode_guard(caller).gesturing_allowed() is True


def test_an_unset_action_id_with_any_other_description_is_refused(rig):
    from aimdk_msgs.msg import McActionStatus

    robot, caller = rig
    robot.current_action = 0
    robot.action_desc = 'DAMPING_DEFAULT'
    robot.action_status = McActionStatus.RUNNING

    assert mode_guard(caller).gesturing_allowed() is False


def test_an_unset_action_id_with_no_description_at_all_is_refused(rig):
    # Nothing said is not the same as "standing".
    from aimdk_msgs.msg import McActionStatus

    robot, caller = rig
    robot.current_action = 0
    robot.action_desc = ''
    robot.action_status = McActionStatus.RUNNING

    assert mode_guard(caller).gesturing_allowed() is False


def test_a_real_non_stand_id_wins_over_a_stand_default_description(rig):
    """The description is a fallback for an unset id, never an override. A
    controller reporting a genuine non-stand mode must still be believed even
    if the description disagrees -- otherwise a stale string could talk the
    interlock out of a refusal.
    """
    from aimdk_msgs.msg import McAction, McActionStatus

    robot, caller = rig
    robot.current_action = McAction.DAMPING_DEFAULT
    robot.action_desc = 'STAND_DEFAULT'
    robot.action_status = McActionStatus.RUNNING

    assert mode_guard(caller).gesturing_allowed() is False


# ------------------------------------------------- a mode still being changed

def test_a_transitioning_mode_is_refused_even_when_the_id_says_stand_default(rig):
    # Mid-switch the controller is moving the robot between stances; swinging
    # an arm into that is exactly what the interlock exists to prevent.
    from aimdk_msgs.msg import McAction, McActionStatus

    robot, caller = rig
    robot.current_action = McAction.STAND_DEFAULT
    robot.action_status = McActionStatus.TRANSITION

    assert mode_guard(caller).gesturing_allowed() is False


def test_a_transitioning_mode_is_refused_on_the_description_path_too(rig):
    from aimdk_msgs.msg import McActionStatus

    robot, caller = rig
    robot.current_action = 0
    robot.action_desc = 'STAND_DEFAULT'
    robot.action_status = McActionStatus.TRANSITION

    assert mode_guard(caller).gesturing_allowed() is False


def test_an_idle_status_does_not_by_itself_refuse(rig):
    # IDLE is McActionStatus's own zero value, so an unset status must not be
    # read as a refusal -- only an explicit TRANSITION is.
    from aimdk_msgs.msg import McAction, McActionStatus

    robot, caller = rig
    robot.current_action = McAction.STAND_DEFAULT
    robot.action_status = McActionStatus.IDLE

    assert mode_guard(caller).gesturing_allowed() is True
