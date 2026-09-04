"""Putting the vendor agent into only_voice mode, and confirming it took.

This adapter was written believing there is no GetAgentProperties service --
that only_voice could be set and its response checked, but never confirmed,
and that the real check was hearing the vendor answer over us with a human
standing there.

On an X2 running the v1.0 image that is not true.
`/aimdk_5Fmsgs/srv/GetAgentPropertiesRequest` is advertised, its Request
takes `property_ids` and its Response carries `contents: AgentProperties`.
Asked for AGENT_PROPERTY_RUN_MODE it answered 'normal' -- which is the
vendor agent fully enabled, running its own dialogue, and is exactly the
state that had to be inferred from the robot talking over us.

So the mode is now read back after it is set. This matters most in the case
the code already had to guess at: a response whose status the vendor leaves
UNKNOWN was being treated as accepted with a warning, and there was no way
to tell that apart from a silent refusal. Now there is.

A robot that does not advertise the read service falls back to the original
behaviour and says so.

Verified against the SDK's own .srv/.msg files (Ruling R13), not the vendor
prose that first described this service:
- SERVICE follows the '/aimdk_5Fmsgs/srv/<Type>' convention every other
  service in this package uses (ros/speech.py, ros/gesture.py), not the
  human-readable path a draft of this adapter once assumed.
- SetAgentPropertiesRequest.Request has `header: CommonRequest` and
  `contents: AgentProperties`, where AgentProperties is a list of
  AgentPropertiesValue(key: AgentPropertyIdType, value: str). There is no
  `only_voice` field -- only_voice is the *value* of the run-mode property.
- SetAgentPropertiesRequest.Response has a single field, `header`, typed
  CommonResponse (header: ResponseHeader, status: CommonState, message:
  str) despite the name -- the same shape SetMcPresetMotion's `response`
  field and PlayAudioFile's `reponse` field carry. The real answer is in
  `response.header.status.value`, matching the hardware-proven pattern in
  ros/gesture.py:61-76: header.code defaults to zero, so checking only that
  would read every unpopulated response as success.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import rclpy
from aimdk_msgs.msg import AgentPropertiesValue, AgentPropertyIdType, CommonState
from aimdk_msgs.srv import SetAgentPropertiesRequest

try:                                   # not on every image -- see the docstring
    from aimdk_msgs.srv import GetAgentPropertiesRequest
except ImportError:                    # pragma: no cover - image-dependent
    GetAgentPropertiesRequest = None

SERVICE = '/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest'
READ_SERVICE = '/aimdk_5Fmsgs/srv/GetAgentPropertiesRequest'
ONLY_VOICE = 'only_voice'

# CommonResponse.status values that mean the request was accepted, matching
# the accepted set ros/gesture.py and ros/speech.py already use for the
# other CommonResponse/CommonTaskResponse-shaped services.
_ACCEPTED_STATES = (CommonState.SUCCESS, CommonState.PENDING, CommonState.CREATED,
                    CommonState.RUNNING)


def _default_spin(node, future, timeout_sec=None):
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)


class AgentMode:
    def __init__(self, node, service: str = SERVICE, timeout_s: float = 2.0,
                 attempts: int = 8, retry_s: float = 0.25,
                 spin_until: Optional[Callable] = None,
                 callback_group=None, read_service: str = READ_SERVICE) -> None:
        self._node = node
        self._timeout_s = float(timeout_s)
        self._attempts = int(attempts)
        self._retry_s = float(retry_s)
        self._spin = spin_until or _default_spin
        self.available = False
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._client = node.create_client(SetAgentPropertiesRequest, service,
                                          **kwargs)
        self._read_client = None
        if GetAgentPropertiesRequest is not None:
            self._read_client = node.create_client(
                GetAgentPropertiesRequest, read_service, **kwargs)

    def read_run_mode(self, timeout_s: Optional[float] = None) -> Optional[str]:
        """The vendor agent's current run mode, or None if it cannot be read.

        None means "unknown", never "normal": a robot whose image does not
        advertise the read service, an unreachable service and a dropped
        response all land here, and none of them is evidence about what the
        agent is doing.
        """
        if self._read_client is None:
            return None
        timeout = self._timeout_s if timeout_s is None else float(timeout_s)
        if not self._read_client.wait_for_service(timeout_sec=self._retry_s):
            return None

        request = GetAgentPropertiesRequest.Request()
        wanted = AgentPropertyIdType()
        wanted.value = AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE
        request.property_ids.append(wanted)

        future = self._read_client.call_async(request)
        self._spin(self._node, future, timeout_sec=timeout)
        response = future.result()
        if response is None:
            return None
        for item in response.contents.properties:
            if item.key.value == AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE:
                return item.value
        return None

    def _confirm_only_voice(self) -> bool:
        """Read the mode back. Returns False only for a mode we can read and
        that is not only_voice -- an unreadable mode is not a refusal."""
        log = self._node.get_logger()
        mode = self.read_run_mode()
        if mode is None:
            log.warning(
                'the vendor agent run mode could not be read back, so '
                'only_voice is unconfirmed. If the robot answers on its own, '
                'that is why.')
            return True
        if mode == ONLY_VOICE:
            log.info(f'vendor agent run mode confirmed: {mode}')
            return True
        log.error(
            f'the vendor agent accepted only_voice and then reported '
            f'run mode {mode!r}. Its own dialogue is still running and it '
            f'will answer over us.')
        return False

    def set_only_voice(self) -> bool:
        log = self._node.get_logger()
        # Cross-host services are unreliable here; the vendor's own examples
        # retry 8 times at 0.25 s, so we do the same.
        for attempt in range(self._attempts):
            if self._client.wait_for_service(timeout_sec=self._retry_s):
                break
            if self._retry_s:
                time.sleep(self._retry_s)
        else:
            log.error(
                'could not reach the vendor agent service; only_voice was '
                'NOT set. The vendor agent may answer over us.')
            return False

        request = SetAgentPropertiesRequest.Request()
        run_mode = AgentPropertyIdType()
        run_mode.value = AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE
        only_voice = AgentPropertiesValue()
        only_voice.key = run_mode
        only_voice.value = 'only_voice'
        request.contents.properties = [only_voice]

        future = self._client.call_async(request)
        self._spin(self._node, future, timeout_sec=self._timeout_s)
        response = future.result()
        if response is None:
            log.error('setting only_voice timed out; the vendor agent mode '
                      'is unknown and cannot be read back')
            return False

        # response.header is a CommonResponse, not a ResponseHeader -- see
        # the module docstring. The real answer lives in status, not
        # header.code, which defaults to zero.
        common = response.header
        status_value = common.status.value
        if status_value in _ACCEPTED_STATES:
            log.info('vendor agent set to only_voice')
            self.available = True
            return self._confirm_only_voice()
        if status_value == CommonState.UNKNOWN and common.header.code == 0:
            # The vendor left the status unset rather than reporting one --
            # treated as accepted, the same as gesture.py's UNKNOWN+clean
            # header case, but worth a log since there is no way to confirm
            # only_voice ever actually took effect.
            log.warning(
                'vendor agent left the only_voice response status unset '
                '(UNKNOWN, header.code=0); treating it as accepted')
            self.available = True
            # The case this read-back was added for: an unset status is not
            # an answer, so ask the agent what mode it is actually in.
            return self._confirm_only_voice()

        log.error(
            f'the vendor agent refused only_voice: status={status_value}, '
            f'header.code={common.header.code}, message={common.message!r}. '
            'The vendor agent may answer over us.')
        return False
