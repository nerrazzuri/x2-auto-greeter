"""Registering this node as an MC input source.

The controller arbitrates between named control sources and, per the SDK docs
(5.1.3, "Arbitration Workflow"), *discards commands from disabled or unknown
input sources*. The built-in sources are rc (80), vr (70), app_proxy (60),
interaction (50) and pnc (40); the documented band for SDK-developed sources
is 20-39, and that is where we stay.

Staying below the remote controller is deliberate, not a limitation. The
arbiter accepts the highest-priority *active* source, and a source that has
been silent for its timeout is treated as lowest priority and re-arbitrated.
So with the greeter at 30 the operator keeps the remote as an override: pick
it up and it wins at 80; put it down and, one timeout later, the greeter is
selected again. Registering above 80 would take that away — the docs reserve
100-80 for emergency stop and safety modes.

**What is not established:** `SetMcPresetMotion.Request` carries no source
field (only header/area/motion/interrupt/ani_path/play_timestamp), so nothing
in the request identifies the sender. Whether arbitration gates preset motions
at all, or only the continuous locomotion channel the built-in sources drive,
is not stated in the docs and could not be tested — the robot was in the
`Business` system state, where the MC services do not answer at all. Every
failure here is therefore logged and swallowed: if registration turns out to
be unnecessary, refusing to start would be the worse bug.
"""
from __future__ import annotations

from typing import Optional

from aimdk_msgs.msg import CommonState, McInputAction, McInputSource, RequestHeader
from aimdk_msgs.srv import SetMcInputSource

from x2_greeter.ros.service_call import call_with_retry

INPUT_SOURCE_SERVICE = '/aimdk_5Fmsgs/srv/SetMcInputSource'

# The band the SDK docs assign to developer-defined sources. Registering
# outside it is a decision about who can override the robot, so it is refused
# rather than clamped.
SDK_PRIORITY_MIN = 20
SDK_PRIORITY_MAX = 39

# The remote controller's priority. Kept here so the reason for the ceiling is
# visible at the point that enforces it.
RC_PRIORITY = 80

_ACCEPTED_STATES = (CommonState.SUCCESS, CommonState.PENDING, CommonState.CREATED,
                    CommonState.RUNNING)


class PriorityOutOfBand(ValueError):
    """Raised for a priority outside the documented SDK band."""


def check_priority(priority: int) -> int:
    """Validate a configured priority, or raise PriorityOutOfBand."""
    priority = int(priority)
    if SDK_PRIORITY_MIN <= priority <= SDK_PRIORITY_MAX:
        return priority
    raise PriorityOutOfBand(
        f'mc_input.priority {priority} is outside the documented SDK band '
        f'{SDK_PRIORITY_MIN}-{SDK_PRIORITY_MAX}. Values at or above '
        f'{RC_PRIORITY} would outrank the remote controller, which must stay '
        f'able to override the greeter.')


class McInputSourceRegistrar:
    """Registers, and on shutdown removes, this node's MC input source."""

    def __init__(self, node, name: str = 'x2_greeter', priority: int = 30,
                 timeout_ms: int = 1000, callback_group=None) -> None:
        self._node = node
        self._name = str(name)
        self._priority = check_priority(priority)
        self._timeout_ms = int(timeout_ms)
        self._registered = False
        self._client = node.create_client(SetMcInputSource, INPUT_SOURCE_SERVICE,
                                          callback_group=callback_group)

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def name(self) -> str:
        return self._name

    def register(self) -> bool:
        """Add the source, falling back to modifying an existing one.

        A second ADD of the same name is rejected by the controller — the
        vendor example names that case explicitly — and a greeter restarted
        without a clean shutdown will hit it every time, so MODIFY is the
        normal path after a crash, not an edge case.
        """
        if self._request(McInputAction.INPUTACTION_ADD, 'add'):
            self._registered = True
            return True
        self._node.get_logger().info(
            f'input source {self._name!r} could not be added (already present '
            f'from an earlier run?); trying to modify it instead')
        if self._request(McInputAction.INPUTACTION_MODIFY, 'modify'):
            self._registered = True
            return True
        self._node.get_logger().warning(
            f'could not register input source {self._name!r}; the controller '
            f'may discard our motion commands')
        return False

    def deregister(self) -> bool:
        """Remove the source so a later run gets a clean ADD."""
        if not self._registered:
            return True
        ok = self._request(McInputAction.INPUTACTION_DELETE, 'delete')
        self._registered = not ok
        return ok

    def _request(self, action_value: int, verb: str) -> bool:
        request = SetMcInputSource.Request()
        request.request.header = RequestHeader()

        action = McInputAction()
        action.value = int(action_value)
        request.action = action

        source = McInputSource()
        source.name = self._name
        source.priority = self._priority
        source.timeout = self._timeout_ms
        request.input_source = source

        def stamp(req):
            req.request.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._node.get_logger().warning(
                f'SetMcInputSource ({verb}) did not respond')
            return False
        return self._accepted(response, verb)

    def _accepted(self, response, verb: str) -> bool:
        """Read the answer out of `state`, not `header.code`.

        CommonTaskResponse.state is the field this response exists to carry.
        header.code is a default-zero integer, so a controller that refuses
        the request through state alone reads as success to anything that
        trusts the header — the mistake this codebase already shipped once.
        The one concession is a controller that fills only the header:
        UNKNOWN state with a clean code is the best signal it gives us.
        """
        state_value = response.response.state.value
        task_id = response.response.task_id
        if state_value in _ACCEPTED_STATES:
            self._node.get_logger().info(
                f'input source {self._name!r} {verb} accepted at priority '
                f'{self._priority} (task {task_id})')
            return True
        if state_value == CommonState.UNKNOWN and response.response.header.code == 0:
            self._node.get_logger().info(
                f'input source {self._name!r} {verb} accepted at priority '
                f'{self._priority} (task {task_id}, header-only response)')
            return True
        self._node.get_logger().info(
            f'input source {self._name!r} {verb} rejected: state={state_value} '
            f'(task {task_id})')
        return False


def build_registrar(node, enabled: bool, name: str, priority: int, timeout_ms: int,
                    callback_group=None) -> Optional[McInputSourceRegistrar]:
    """Build a registrar, or return None when disabled or misconfigured.

    Does not register. Registering is a service call, and call_with_retry
    waits on a future that only a *spinning* executor completes -- so it must
    not run from a node's constructor, where nothing spins yet. GreetingNode
    registers from a timer once it does.

    Never raises: a greeter that cannot register its input source should still
    come up and speak. Speech goes through the audio module, which arbitrates
    separately, so the degraded case is the same one the gesture interlocks
    already produce — it talks, it does not gesture.
    """
    if not enabled:
        node.get_logger().info('mc_input.enabled is false; not registering an input source')
        return None
    try:
        registrar = McInputSourceRegistrar(
            node, name=name, priority=priority, timeout_ms=timeout_ms,
            callback_group=callback_group)
    except PriorityOutOfBand as exc:
        node.get_logger().error(f'{exc} Not registering an input source.')
        return None
    return registrar
