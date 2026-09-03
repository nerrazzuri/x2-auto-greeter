"""Is it safe to gesture right now?

Preset motions require force-control stand (STAND_DEFAULT). This asks, and
never tells: auto-transitioning a humanoid into force-control stand is how a
robot falls over, and the docs require both feet planted first. That stays a
human decision (spec section 11).

Not knowing the mode is not the same as knowing it is safe, so an unreachable
service refuses.
"""
from __future__ import annotations

from typing import Optional

from aimdk_msgs.msg import McAction, McActionStatus
from aimdk_msgs.srv import GetMcAction

from x2_greeter.ros.service_call import call_with_retry

GET_ACTION_SERVICE = '/aimdk_5Fmsgs/srv/GetMcAction'


class ModeGuard:
    def __init__(self, node, require_stand_default: bool = True,
                 callback_group=None) -> None:
        self._node = node
        self._require_stand_default = bool(require_stand_default)
        self._last_action: Optional[int] = None
        self._last_desc: str = ''
        self._warned = False
        self._client = node.create_client(GetMcAction, GET_ACTION_SERVICE,
                                          callback_group=callback_group)

    @property
    def last_action(self) -> Optional[int]:
        return self._last_action

    def gesturing_allowed(self) -> bool:
        if not self._require_stand_default:
            return True

        request = GetMcAction.Request()

        def stamp(req):
            req.request.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._warn_once('cannot read the robot motion mode; not gesturing')
            self._last_action = None
            self._last_desc = ''
            return False

        self._last_action = int(response.info.current_action.value)
        self._last_desc = (response.info.action_desc or '').strip()
        status = int(response.info.status.value)

        # A mode that is still changing is not a mode we may act on, whatever
        # it currently reads as: the arm would swing while the controller is
        # moving the robot between stances.
        if status == McActionStatus.TRANSITION:
            self._warn_once(
                f'motion mode is still changing (status TRANSITION, currently '
                f'{self._describe()}); speaking but not gesturing')
            return False

        if self._last_action == McAction.STAND_DEFAULT:
            self._warned = False
            return True

        # McAction.value is a default-zero field, and this robot's controller
        # leaves it at zero while naming the real mode in action_desc -- the
        # mirror image of the ResponseHeader.code trap this codebase already
        # shipped once. Zero is not a mode: the enum starts at 1, so a zero
        # here means "not populated", not "some other stance". In that one
        # case the description is the only signal the controller gives, and
        # refusing it would make the gesture unreachable on this hardware
        # forever. Any *non-zero* value is a real answer and is trusted over
        # the description, so this can never talk the guard out of a refusal.
        if self._last_action == 0 and self._describes_stand_default():
            self._warn_once(
                'controller left McAction.value unset and reports the mode only '
                f'as action_desc={self._last_desc!r}; treating that as '
                'STAND_DEFAULT')
            return True

        self._warn_once(
            f'robot is in motion mode {self._describe()}, not STAND_DEFAULT '
            f'({McAction.STAND_DEFAULT}); speaking but not gesturing')
        return False

    def _describes_stand_default(self) -> bool:
        return self._last_desc.upper() == 'STAND_DEFAULT'

    def _describe(self) -> str:
        if self._last_desc:
            return f'{self._last_action} ({self._last_desc})'
        return str(self._last_action)

    def _warn_once(self, message: str) -> None:
        if self._warned:
            self._node.get_logger().debug(message)
            return
        self._node.get_logger().warning(message)
        self._warned = True
