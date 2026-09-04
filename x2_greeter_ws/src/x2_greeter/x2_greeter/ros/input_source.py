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
        """Modify the source, falling back to adding it.

        MODIFY first, which is the opposite of what the vendor example does,
        and the order matters on hardware.

        A source that already exists is the normal case, not an edge case: any
        restart without a clean shutdown leaves it behind, and after the first
        run of this node on a given robot it is always there. In that state the
        controller does not *reject* a second ADD -- it never answers it at
        all. Measured on an X2: ADD produced no response in 15 s, while MODIFY
        answered in 1 ms.

        Worse, the unanswered ADDs jam the service for what follows.
        `call_with_retry` sends eight of them, and the MODIFY behind them was
        then dropped eight times in a row -- so the fallback that exists for
        exactly this case could never run, and the node came up every time
        saying "the controller may discard our motion commands". The same
        MODIFY, sent first through the same machinery, answered in 6 ms.

        On a controller that has never seen this source, MODIFY is the one
        that fails and ADD is the fallback -- one wasted round trip on the
        first run of a robot's life, against a permanent failure on every run
        after it.
        """
        if self._request(McInputAction.INPUTACTION_MODIFY, 'modify'):
            self._registered = True
            return True
        self._node.get_logger().info(
            f'input source {self._name!r} could not be modified (not present '
            f'yet?); adding it instead')
        if self._request(McInputAction.INPUTACTION_ADD, 'add'):
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


def maybe_register(node, enabled: bool, name: str, priority: int, timeout_ms: int,
                   callback_group=None,
                   register_now: bool = True) -> Optional[McInputSourceRegistrar]:
    """Build a source registrar, and register unless told to wait.

    Never raises: a greeter that cannot register its input source should still
    come up and speak. Speech goes through the audio module, which arbitrates
    separately, so the degraded case is the same one the gesture interlocks
    already produce — it talks, it does not gesture.

    `register_now=False` builds the registrar without calling anything, so a
    node can create the service client during construction and do the call
    later. That split is not tidiness. rclpy's executor collects a node's
    entities when it starts spinning and rebuilds only on a graph event, so a
    client created *inside* a callback of an already-spinning executor can
    have its responses go unprocessed. Measured on an X2: the identical
    request through the identical machinery answered in 4 ms from a client
    made before spin(), and timed out on all eight attempts from one made
    after it — while a standalone probe against the same live service, at the
    same moment, kept answering in milliseconds.
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
    if register_now:
        registrar.register()
    return registrar
