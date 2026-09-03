"""The face adapter. A failed expression is never a failed turn.

Corrected against the SDK's own PlayEmoji.srv (Ruling R14), not the task
brief's prose:
- SERVICE is '/aimdk_5Fmsgs/srv/PlayEmoji', matching the
  '/aimdk_5Fmsgs/srv/<Type>' convention every other service in this package
  uses (ros/speech.py, ros/gesture.py, ros/agent_mode.py).
- The response carries header: CommonResponse, bool success, string message
  -- an explicit success flag, not a code to compare against zero.
  header.code defaults to zero, so checking only that would read every
  unpopulated response as success; response.success is checked instead, and
  fakes here set it directly rather than poking header.code.

Imports of aimdk_msgs/x2_greeter.ros are deferred into functions rather than
done at module scope, matching every other file in test/ros/ -- aimdk_msgs
is only installed in the Docker harness, and a module-level import would
turn "deselected on host" into a collection error.
"""
import pytest

from x2_greeter.core.faces import CATALOGUE, MODE_LOOP, MODE_ONCE

pytestmark = pytest.mark.ros


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _FakeClient:
    def __init__(self, ready=True, results=None, raises=None):
        self.ready = ready
        self.sent = []
        self.raises = raises
        self._results = list(results or [])

    def wait_for_service(self, timeout_sec=None):
        return self.ready

    def call_async(self, request):
        if self.raises is not None:
            raise self.raises
        self.sent.append(request)
        return _FakeFuture(self._results.pop(0) if self._results else None)


class _FakeNode:
    def __init__(self, client):
        self.client = client
        self.logs = []

    def create_client(self, srv_type, name, **kwargs):
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
            def debug(self, m): node.logs.append(m)
        return _L()


def _ok():
    from aimdk_msgs.srv import PlayEmoji

    response = PlayEmoji.Response()
    response.success = True
    return response


def _face_class():
    from x2_greeter.ros.face import Face
    return Face


def _spin(node, future, timeout_sec=None):
    return None


def _face(client, **kwargs):
    kwargs.setdefault('enabled', True)
    return _face_class()(node=_FakeNode(client), spin_until=_spin, **kwargs)


def test_it_targets_the_documented_service():
    from aimdk_msgs.srv import PlayEmoji

    from x2_greeter.ros.face import SERVICE

    client = _FakeClient(results=[_ok()])
    node = _FakeNode(client)
    _face_class()(node=node, enabled=True, spin_until=_spin)
    assert node.service_name == SERVICE
    assert SERVICE == '/aimdk_5Fmsgs/srv/PlayEmoji'
    assert node.srv_type is PlayEmoji


def test_a_disabled_face_sends_nothing_and_says_so():
    client = _FakeClient(results=[_ok()])
    face = _face_class()(node=_FakeNode(client), enabled=False, spin_until=_spin)
    assert face.show('happy') is False
    assert client.sent == [], 'disabled means no service call at all'


def test_showing_an_expression_sends_the_catalogued_id():
    client = _FakeClient(results=[_ok()])
    assert _face(client).show('happy') is True
    assert client.sent[0].emotion_id == CATALOGUE['happy'].emotion_id


def test_the_default_mode_is_once_not_loop():
    client = _FakeClient(results=[_ok()])
    _face(client).show('happy')
    assert client.sent[0].mode == MODE_ONCE, (
        'a looping expression outlives the sentence that prompted it')


def test_loop_mode_is_available_when_asked_for():
    client = _FakeClient(results=[_ok()])
    _face(client).show('calm', mode=MODE_LOOP)
    assert client.sent[0].mode == MODE_LOOP


def test_the_thinking_face_has_its_own_one_call_path():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.show_thinking() is True
    assert client.sent[0].emotion_id == CATALOGUE['thinking'].emotion_id


def test_an_unknown_expression_name_is_refused_without_a_call():
    client = _FakeClient(results=[_ok()])
    assert _face(client).show('smouldering') is False
    assert client.sent == []


def test_the_last_shown_expression_is_remembered():
    client = _FakeClient(results=[_ok(), _ok()])
    face = _face(client)
    face.show('happy')
    face.show('confused')
    assert face.last_shown == 'confused'


def test_a_service_that_is_not_there_returns_false_and_does_not_raise():
    face = _face(_FakeClient(ready=False), attempts=2, retry_s=0.0)
    assert face.show('happy') is False


def test_a_timeout_returns_false():
    assert _face(_FakeClient(results=[None])).show('happy') is False


def test_an_unsuccessful_response_returns_false():
    bad = _ok()
    bad.success = False
    bad.message = 'busy'
    face = _face(_FakeClient(results=[bad]))
    assert face.show('happy') is False
    assert any('busy' in log for log in face._node.logs)


def test_an_exception_from_the_client_is_swallowed():
    # The words are the turn. Nothing the face does may interrupt them.
    face = _face(_FakeClient(raises=RuntimeError('transport died')))
    assert face.show('happy') is False


def test_clear_returns_the_face_to_its_idle_expression():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.clear() is True
    assert client.sent[0].emotion_id == CATALOGUE['blink'].emotion_id


def test_availability_is_false_until_a_call_has_actually_worked():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.available is False
    face.show('happy')
    assert face.available is True
