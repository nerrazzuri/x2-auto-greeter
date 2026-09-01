import base64
import json

import anthropic
import pytest

from x2_greeter.cognition.claude import SYSTEM_PROMPT, ClaudeBackend, build_schema
from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import JpegFrame, SceneContext

ENABLED = ('wave', 'salute', 'bow')
CTX = SceneContext(distance_m=2.1, center_offset=-0.12)
FRAME = JpegFrame(data=b'\xff\xd8\xff\xe0fakejpegbytes')

GOOD_PAYLOAD = {
    'person_present': True,
    'facing_robot': True,
    'confidence': 0.93,
    'greeting': 'Hello! Welcome, good to see you.',
    'gesture': 'wave',
    'reason': 'an adult standing squarely in front of the camera',
}


class TextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class ThinkingBlock:
    type = 'thinking'
    thinking = 'considering'


class FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.raises is not None:
            raise self._owner.raises
        return self._owner.response


class FakeClient:
    """Stands in for anthropic.Anthropic: records calls, returns canned blocks."""

    def __init__(self, payload=None, raises=None, blocks=None):
        self.calls = []
        self.options = []
        self.raises = raises
        if blocks is None:
            blocks = [ThinkingBlock(), TextBlock(json.dumps(payload if payload is not None
                                                            else GOOD_PAYLOAD))]
        self.response = type('Resp', (), {'content': blocks})()
        self.messages = FakeMessages(self)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


def backend(client, **kwargs):
    return ClaudeBackend(enabled_gestures=ENABLED, client=client, **kwargs)


def test_it_satisfies_the_protocol():
    assert isinstance(backend(FakeClient()), GreetingBackend)


def test_a_good_response_becomes_a_verdict():
    verdict = backend(FakeClient()).confirm_and_compose(FRAME, CTX)
    assert verdict.person_present is True
    assert verdict.facing_robot is True
    assert verdict.confidence == pytest.approx(0.93)
    assert verdict.greeting == 'Hello! Welcome, good to see you.'
    assert verdict.gesture == 'wave'
    assert verdict.source == 'claude'


def test_it_sends_the_opus_5_model_and_low_effort():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    kwargs = client.calls[0]
    assert kwargs['model'] == 'claude-opus-5'
    assert kwargs['output_config']['effort'] == 'low'


def test_it_leaves_adaptive_thinking_on():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    assert client.calls[0]['thinking'] == {'type': 'adaptive'}


def test_it_never_sends_budget_tokens():
    # budget_tokens is rejected with a 400 on Opus 5.
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    assert 'budget_tokens' not in json.dumps(client.calls[0]['thinking'])


def test_it_applies_a_hard_per_call_timeout_with_no_retries():
    client = FakeClient()
    backend(client, timeout_s=2.5).confirm_and_compose(FRAME, CTX)
    assert client.options[0] == {'timeout': 2.5, 'max_retries': 0}


def test_it_sends_the_frame_as_base64_jpeg():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    blocks = client.calls[0]['messages'][0]['content']
    image = next(b for b in blocks if b['type'] == 'image')
    assert image['source']['type'] == 'base64'
    assert image['source']['media_type'] == 'image/jpeg'
    assert base64.standard_b64decode(image['source']['data']) == FRAME.data


def test_it_tells_the_model_where_the_person_is():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    blocks = client.calls[0]['messages'][0]['content']
    text = next(b for b in blocks if b['type'] == 'text')['text']
    assert '2.1' in text
    assert 'left' in text.lower()


def test_the_schema_enum_is_exactly_the_enabled_gestures():
    schema = build_schema(ENABLED)
    assert schema['properties']['gesture']['enum'] == list(ENABLED)
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == {
        'person_present', 'facing_robot', 'confidence', 'greeting', 'gesture', 'reason'}


def test_the_request_carries_that_schema():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    fmt = client.calls[0]['output_config']['format']
    assert fmt['type'] == 'json_schema'
    assert fmt['schema']['properties']['gesture']['enum'] == list(ENABLED)


def test_a_gesture_outside_the_allowlist_is_dropped_not_trusted():
    payload = dict(GOOD_PAYLOAD, gesture='grab_buttocks')
    verdict = backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)
    assert verdict.gesture is None          # selector will choose at random
    assert verdict.greeting == GOOD_PAYLOAD['greeting']


def test_a_negative_verdict_is_passed_through():
    payload = dict(GOOD_PAYLOAD, person_present=False, greeting='',
                   reason='a coat on a stand')
    verdict = backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)
    assert verdict.person_present is False


def test_an_empty_greeting_on_a_positive_verdict_is_unusable():
    payload = dict(GOOD_PAYLOAD, greeting='   ')
    with pytest.raises(BackendUnavailable, match='empty greeting'):
        backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)


def test_malformed_json_raises_backend_unavailable():
    blocks = [TextBlock('{not json at all')]
    with pytest.raises(BackendUnavailable, match='malformed'):
        backend(FakeClient(blocks=blocks)).confirm_and_compose(FRAME, CTX)


def test_a_response_with_no_text_block_raises_backend_unavailable():
    with pytest.raises(BackendUnavailable, match='no text'):
        backend(FakeClient(blocks=[ThinkingBlock()])).confirm_and_compose(FRAME, CTX)


def test_a_missing_required_field_raises_backend_unavailable():
    payload = {k: v for k, v in GOOD_PAYLOAD.items() if k != 'person_present'}
    with pytest.raises(BackendUnavailable, match='person_present'):
        backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)


def test_a_missing_frame_raises_rather_than_guessing():
    with pytest.raises(BackendUnavailable, match='no frame'):
        backend(FakeClient()).confirm_and_compose(None, CTX)


def _api_error(cls, status_code):
    request = type('Req', (), {})()
    response = type('Resp', (), {'status_code': status_code, 'headers': {}, 'request': request})()
    return cls('boom', response=response, body=None)


def test_a_timeout_raises_backend_unavailable():
    err = anthropic.APITimeoutError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable, match='timed out'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_a_rate_limit_raises_backend_unavailable():
    err = _api_error(anthropic.RateLimitError, 429)
    with pytest.raises(BackendUnavailable, match='rate limited'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_an_unknown_model_raises_backend_unavailable():
    err = _api_error(anthropic.NotFoundError, 404)
    with pytest.raises(BackendUnavailable, match='not found'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_a_network_failure_raises_backend_unavailable():
    err = anthropic.APIConnectionError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable, match='unreachable'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_an_unexpected_exception_still_raises_backend_unavailable():
    with pytest.raises(BackendUnavailable):
        backend(FakeClient(raises=RuntimeError('surprise'))).confirm_and_compose(FRAME, CTX)


def test_the_system_prompt_says_orientation_is_not_a_gate():
    assert 'back' in SYSTEM_PROMPT.lower()
    assert 'greet' in SYSTEM_PROMPT.lower()


def test_image_bytes_never_appear_in_an_exception_message():
    err = anthropic.APIConnectionError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable) as excinfo:
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)
    assert 'fakejpegbytes' not in str(excinfo.value)
