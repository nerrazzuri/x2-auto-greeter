"""Putting the vendor agent into only_voice mode.

There is no GetAgentProperties service -- the interaction srv directory
holds exactly GetMicSourceRequest, PlayTts, SetAgentPropertiesRequest and
SetMicSourceRequest. So this can be set and its response checked, and then
never confirmed again. If the vendor agent is still running its own
dialogue you will hear it answer over us; that is the real check, and it
happens with a human standing there.

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

SERVICE = '/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest'

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
                 callback_group=None) -> None:
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
            return True
        if status_value == CommonState.UNKNOWN and common.header.code == 0:
            # The vendor left the status unset rather than reporting one --
            # treated as accepted, the same as gesture.py's UNKNOWN+clean
            # header case, but worth a log since there is no way to confirm
            # only_voice ever actually took effect.
            log.warning(
                'vendor agent left the only_voice response status unset '
                '(UNKNOWN, header.code=0); treating it as accepted')
            log.info('vendor agent set to only_voice')
            self.available = True
            return True

        log.error(
            f'the vendor agent refused only_voice: status={status_value}, '
            f'header.code={common.header.code}, message={common.message!r}. '
            'The vendor agent may answer over us.')
        return False
