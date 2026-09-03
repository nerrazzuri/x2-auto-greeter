"""The MC input source registrar, against the fake controller's registry."""
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
    caller = ros.create_node('input_source_caller')
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


def registrar(caller, **kwargs):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.input_source import McInputSourceRegistrar

    kwargs.setdefault('callback_group', ReentrantCallbackGroup())
    return McInputSourceRegistrar(caller, **kwargs)


# --------------------------------------------------------------- registering

def test_register_adds_the_source_at_the_configured_priority(rig):
    robot, caller = rig
    reg = registrar(caller, name='x2_greeter', priority=30)

    assert reg.register() is True
    assert reg.registered is True
    assert robot.input_sources == {'x2_greeter': 30}


def test_register_sends_the_configured_timeout(rig):
    robot, caller = rig
    registrar(caller, name='x2_greeter', priority=30, timeout_ms=1500).register()

    assert robot.input_source_requests[0].input_source.timeout == 1500


def test_register_falls_back_to_modify_when_the_name_already_exists(rig):
    # A greeter killed without a clean shutdown leaves its source behind, so
    # the next run's ADD is rejected. That is the normal restart path.
    robot, caller = rig
    robot.input_sources['x2_greeter'] = 25

    reg = registrar(caller, name='x2_greeter', priority=30)
    assert reg.register() is True

    from aimdk_msgs.msg import McInputAction
    actions = [r.action.value for r in robot.input_source_requests]
    assert actions == [McInputAction.INPUTACTION_ADD, McInputAction.INPUTACTION_MODIFY]
    # MODIFY must actually correct the stale priority, not just succeed.
    assert robot.input_sources['x2_greeter'] == 30


def test_a_controller_that_refuses_both_leaves_the_registrar_unregistered(rig):
    robot, caller = rig
    robot.input_source_mode = 'refuse'
    reg = registrar(caller, name='x2_greeter', priority=30)

    assert reg.register() is False
    assert reg.registered is False
    # Both the ADD and the MODIFY fallback were actually attempted.
    assert len(robot.input_source_requests) == 2


def test_a_refused_registration_does_not_raise(rig):
    # The node must still come up and speak; see maybe_register's docstring.
    robot, caller = rig
    robot.input_source_mode = 'refuse'

    registrar(caller, name='x2_greeter', priority=30).register()  # must not raise


# ------------------------------------------------------------- deregistering

def test_deregister_removes_the_source(rig):
    robot, caller = rig
    reg = registrar(caller, name='x2_greeter', priority=30)
    reg.register()

    assert reg.deregister() is True
    assert robot.input_sources == {}
    assert reg.registered is False


def test_deregister_is_a_no_op_when_registration_never_succeeded(rig):
    robot, caller = rig
    robot.input_source_mode = 'refuse'
    reg = registrar(caller, name='x2_greeter', priority=30)
    reg.register()
    robot.input_source_requests.clear()

    assert reg.deregister() is True
    assert robot.input_source_requests == []


# ------------------------------------------------ the response-contract trap

def test_a_header_only_response_counts_as_accepted(rig):
    # ResponseHeader.code defaults to zero, so `state` is checked first. A
    # controller that fills only the header still has to read as success --
    # UNKNOWN state with a clean code is the best signal it gives us.
    robot, caller = rig
    robot.input_source_mode = 'header_only'

    reg = registrar(caller, name='x2_greeter', priority=30)
    assert reg.register() is True
    # One call only: accepting on the first ADD means no MODIFY fallback ran.
    assert len(robot.input_source_requests) == 1


def test_a_failure_state_is_not_masked_by_a_clean_header(rig):
    # The Critical this codebase already shipped once: header.code is a
    # default-zero field, so a controller that refuses through `state` alone
    # must not read as success.
    robot, caller = rig
    robot.input_source_mode = 'refuse'   # header.code == 0, state == FAILURE

    assert registrar(caller, name='x2_greeter', priority=30).register() is False


# ------------------------------------------------------ the priority ceiling

@pytest.mark.parametrize('priority', [20, 30, 39])
def test_priorities_inside_the_sdk_band_are_accepted(priority):
    from x2_greeter.ros.input_source import check_priority
    assert check_priority(priority) == priority


@pytest.mark.parametrize('priority', [19, 40, 50, 80, 90, 100])
def test_priorities_outside_the_sdk_band_are_refused(priority):
    # 80 is the remote controller. Registering at or above it would take the
    # operator's override away, so the value is refused rather than clamped --
    # silently lowering it would hide a decision about who can stop the robot.
    from x2_greeter.ros.input_source import PriorityOutOfBand, check_priority
    with pytest.raises(PriorityOutOfBand):
        check_priority(priority)


def test_maybe_register_returns_none_and_does_not_raise_on_a_bad_priority(rig):
    robot, caller = rig
    from x2_greeter.ros.input_source import maybe_register

    assert maybe_register(caller, enabled=True, name='x2_greeter', priority=90,
                          timeout_ms=1000) is None
    assert robot.input_source_requests == []


def test_maybe_register_does_nothing_when_disabled(rig):
    robot, caller = rig
    from x2_greeter.ros.input_source import maybe_register

    assert maybe_register(caller, enabled=False, name='x2_greeter', priority=30,
                          timeout_ms=1000) is None
    assert robot.input_source_requests == []


def test_an_unreachable_service_is_not_treated_as_registered(ros):
    # Nothing serves SetMcInputSource here: no fake robot is running.
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.input_source import McInputSourceRegistrar

    caller = ros.create_node('lonely_caller')
    reg = McInputSourceRegistrar(caller, name='x2_greeter', priority=30,
                                 callback_group=ReentrantCallbackGroup())
    try:
        assert reg.register() is False
        assert reg.registered is False
    finally:
        caller.destroy_node()
