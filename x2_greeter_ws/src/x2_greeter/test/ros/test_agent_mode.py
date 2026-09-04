"""Setting only_voice, and being honest about whether it worked.

Corrected against the SDK's own .srv/.msg files (Ruling R13), not the vendor
prose that first described this service:
- SERVICE is '/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest', matching the
  '/aimdk_5Fmsgs/srv/<Type>' convention every other service in this package
  uses (see ros/speech.py, ros/gesture.py).
- The request carries `header: CommonRequest` + `contents: AgentProperties`,
  where AgentProperties is a list of AgentPropertiesValue(key, value); there
  is no `only_voice` field anywhere on the request.
- The response's single field is `header`, but it is typed CommonResponse
  (header: ResponseHeader, status: CommonState, message: str) -- the same
  shape SetMcPresetMotion's `response` field and PlayAudioFile's `reponse`
  field carry (see ros/gesture.py:61-76 and ros/speech.py:140-165). The real
  answer is in `response.header.status.value`, not `response.header.code`,
  which defaults to zero and would read every unpopulated response as
  success.

Imports of aimdk_msgs/x2_greeter.ros are deferred into functions rather than
done at module scope, matching every other file in test/ros/ -- aimdk_msgs
is only installed in the Docker harness, and a module-level import would
turn "deselected on host" into a collection error.
"""
import pytest

pytestmark = pytest.mark.ros


class _FakeFuture:
    def __init__(self, result=None):
        self._result = result

    def result(self):
        return self._result


class _FakeClient:
    def __init__(self, ready_after=0, results=None):
        self.ready_after = ready_after
        self.wait_calls = 0
        self.sent = []
        self._results = list(results or [])

    def wait_for_service(self, timeout_sec=None):
        self.wait_calls += 1
        return self.wait_calls > self.ready_after

    def call_async(self, request):
        self.sent.append(request)
        return _FakeFuture(self._results.pop(0) if self._results else None)


class _FakeNode:
    """AgentMode creates two clients now -- the setter and the read-back --
    so the fake hands out a different one per service name. `service_name`
    and `srv_type` keep meaning the setter, which is what the older tests
    below assert on."""

    def __init__(self, client, read_client=None):
        self.client = client
        self.read_client = read_client
        self.logs = []
        self.service_names = []
        self.service_name = None
        self.srv_type = None

    def create_client(self, srv_type, name, **kwargs):
        from x2_greeter.ros.agent_mode import READ_SERVICE
        self.service_names.append(name)
        if name == READ_SERVICE:
            return self.read_client if self.read_client is not None else _FakeClient()
        self.srv_type = srv_type
        self.service_name = name
        return self.client

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def _ok_response():
    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetAgentPropertiesRequest

    response = SetAgentPropertiesRequest.Response()
    # response.header is a CommonResponse (header/status/message), not a
    # ResponseHeader -- the field is just unfortunately also named 'header'.
    response.header.status.value = CommonState.SUCCESS
    return response


def _agent_mode(**kwargs):
    from x2_greeter.ros.agent_mode import AgentMode
    return AgentMode(**kwargs)


def _spin_ok(node, future, timeout_sec=None):
    return None


def test_it_targets_the_documented_service():
    from aimdk_msgs.srv import SetAgentPropertiesRequest

    from x2_greeter.ros.agent_mode import SERVICE

    client = _FakeClient(results=[_ok_response()])
    node = _FakeNode(client)
    _agent_mode(node=node, spin_until=_spin_ok)
    assert node.service_name == SERVICE
    assert SERVICE == '/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest'
    assert node.srv_type is SetAgentPropertiesRequest


def test_a_successful_call_reports_true():
    client = _FakeClient(results=[_ok_response()])
    mode = _agent_mode(node=_FakeNode(client), spin_until=_spin_ok)
    assert mode.set_only_voice() is True
    assert len(client.sent) == 1


def test_the_request_carries_the_run_mode_property():
    from aimdk_msgs.msg import AgentPropertyIdType

    client = _FakeClient(results=[_ok_response()])
    mode = _agent_mode(node=_FakeNode(client), spin_until=_spin_ok)
    mode.set_only_voice()
    sent = client.sent[0]
    properties = sent.contents.properties
    assert len(properties) == 1
    assert properties[0].key.value == AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE
    assert properties[0].value == 'only_voice'


def test_a_service_that_never_appears_reports_false_and_does_not_hang():
    client = _FakeClient(ready_after=999)
    mode = _agent_mode(node=_FakeNode(client), attempts=3, retry_s=0.0,
                       spin_until=_spin_ok)
    assert mode.set_only_voice() is False
    assert client.wait_calls == 3, 'bounded retries, matching the vendor examples'


def test_a_timeout_is_not_reported_as_success():
    client = _FakeClient(results=[None])
    mode = _agent_mode(node=_FakeNode(client), spin_until=_spin_ok)
    assert mode.set_only_voice() is False


def test_a_failure_status_is_a_failure():
    from aimdk_msgs.msg import CommonState

    response = _ok_response()
    response.header.status.value = CommonState.FAILURE
    mode = _agent_mode(node=_FakeNode(_FakeClient(results=[response])),
                       spin_until=_spin_ok)
    assert mode.set_only_voice() is False


def test_an_unset_status_with_a_clean_header_is_accepted_but_logged():
    from aimdk_msgs.srv import SetAgentPropertiesRequest

    # Neither status nor header.code touched -- CommonState.UNKNOWN is 0,
    # header.code defaults to 0. This is the "vendor didn't populate it"
    # case Phase 1 already treats as accepted for gestures and audio.
    response = SetAgentPropertiesRequest.Response()
    node = _FakeNode(_FakeClient(results=[response]))
    mode = _agent_mode(node=node, spin_until=_spin_ok)
    assert mode.set_only_voice() is True
    joined = ' '.join(node.logs).lower()
    assert 'unset' in joined or 'unknown' in joined


def test_a_failure_is_logged_loudly_because_nothing_can_check_it_later():
    node = _FakeNode(_FakeClient(ready_after=999))
    _agent_mode(node=node, attempts=1, retry_s=0.0, spin_until=_spin_ok
               ).set_only_voice()
    joined = ' '.join(node.logs).lower()
    assert 'only_voice' in joined
    assert 'vendor' in joined or 'agent' in joined


# ------------------------------------------------- reading the mode back

def _read_response(mode: str):
    """A GetAgentProperties response reporting `mode` as the run mode."""
    from aimdk_msgs.msg import AgentPropertiesValue, AgentPropertyIdType, CommonState
    from aimdk_msgs.srv import GetAgentPropertiesRequest

    response = GetAgentPropertiesRequest.Response()
    response.header.status.value = CommonState.SUCCESS
    item = AgentPropertiesValue()
    key = AgentPropertyIdType()
    key.value = AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE
    item.key = key
    item.value = mode
    response.contents.properties = [item]
    return response


def _unset_response():
    """The vendor leaving the status UNKNOWN with a clean header -- the case
    the read-back exists for, because it is not an answer either way."""
    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import SetAgentPropertiesRequest

    response = SetAgentPropertiesRequest.Response()
    response.header.status.value = CommonState.UNKNOWN
    response.header.header.code = 0
    return response


def test_the_read_client_targets_the_read_service():
    from x2_greeter.ros.agent_mode import READ_SERVICE

    node = _FakeNode(_FakeClient(), _FakeClient())
    _agent_mode(node=node, spin_until=_spin_ok)
    assert READ_SERVICE in node.service_names
    assert READ_SERVICE == '/aimdk_5Fmsgs/srv/GetAgentPropertiesRequest'


def test_the_run_mode_is_read_back_after_it_is_set():
    reader = _FakeClient(results=[_read_response('only_voice')])
    node = _FakeNode(_FakeClient(results=[_ok_response()]), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).set_only_voice() is True
    assert len(reader.sent) == 1, 'the mode was never read back'


def test_a_mode_that_reads_back_as_normal_is_a_failure():
    """The whole point. The vendor accepted the request and stayed in its own
    dialogue; before the read-back this looked like success and the only
    symptom was the robot answering over us."""
    reader = _FakeClient(results=[_read_response('normal')])
    node = _FakeNode(_FakeClient(results=[_ok_response()]), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).set_only_voice() is False
    assert any('normal' in m for m in node.logs)


def test_an_unset_status_is_decided_by_the_read_back():
    reader = _FakeClient(results=[_read_response('normal')])
    node = _FakeNode(_FakeClient(results=[_unset_response()]), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).set_only_voice() is False


def test_an_unset_status_that_reads_back_correctly_is_accepted():
    reader = _FakeClient(results=[_read_response('only_voice')])
    node = _FakeNode(_FakeClient(results=[_unset_response()]), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).set_only_voice() is True


def test_an_unreadable_mode_is_not_treated_as_a_refusal():
    # A robot whose image has no read service, or a dropped response. Neither
    # is evidence that only_voice failed, and refusing to run on that basis
    # would take a working greeter off the road.
    reader = _FakeClient(results=[None])
    node = _FakeNode(_FakeClient(results=[_ok_response()]), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).set_only_voice() is True
    assert any('could not be read back' in m for m in node.logs)


def test_reading_the_mode_returns_what_the_agent_reported():
    reader = _FakeClient(results=[_read_response('no_voice')])
    node = _FakeNode(_FakeClient(), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).read_run_mode() == 'no_voice'


def test_an_unreachable_read_service_reports_unknown_not_a_mode():
    reader = _FakeClient(ready_after=99)
    node = _FakeNode(_FakeClient(), reader)
    assert _agent_mode(node=node, spin_until=_spin_ok).read_run_mode() is None
