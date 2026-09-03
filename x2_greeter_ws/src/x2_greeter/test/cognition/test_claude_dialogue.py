"""The Claude dialogue backend. No network, ever.

The client is injected and every test here uses a stub. What is being tested
is the request we build and the way failures are named -- not Claude.
"""
import json

import pytest

from x2_greeter.cognition.claude import DIALOGUE_SYSTEM_PROMPT, ClaudeDialogueBackend
from x2_greeter.cognition.dialogue import BackendUnavailable, Exchange, TurnLimits
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import parse_venue

GESTURES = ('wave', 'heart')
EMOJI = ('thinking', 'happy')

VENUE = parse_venue({'venue': {
    'kind': 'clothing_store',
    'role': 'a greeter in a clothing store',
    'language_default': 'en',
    'opening': {'en': 'Welcome in.', 'zh': '欢迎光临。'},
    'facts': ['The fitting rooms are at the back.'],
    'topics_encouraged': ['what they are shopping for'],
    'topics_forbidden': ['prices'],
    'deflect_to_human': 'A staff member can help with that.',
}})


class _Frame:
    def __init__(self, data=b'\x89PNG-fake', media_type='image/jpeg'):
        self.data = data
        self.media_type = media_type


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, blocks):
        self.content = blocks


class _StubClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.options = None
        self.request = None
        self.messages = self

    def with_options(self, **kwargs):
        self.options = kwargs
        return self

    def create(self, **kwargs):
        self.request = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


def _ok_response(**overrides):
    doc = {'reply': 'Nice to meet you!', 'language': 'en', 'gesture': 'wave',
           'emoji': 'happy', 'end': False}
    doc.update(overrides)
    return _Response([_Block('thinking'), _Block('text', json.dumps(doc))])


def _scene(distance=1.4, child=False, count=1):
    person = Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                    center_offset=0.0, stature_m=1.0 if child else 1.7,
                    likely_child=child)
    people = tuple([person] * count)
    return SceneSnapshot(people=people, subject=person,
                         mode=AddressingMode.INDIVIDUAL, at_s=0.0)


def _backend(client, **kwargs):
    return ClaudeDialogueBackend(enabled_gestures=GESTURES, enabled_emoji=EMOJI,
                                 client=client, **kwargs)


def _respond(backend, **overrides):
    call = dict(base_frame=_Frame(b'base'), frame=_Frame(b'head'),
                scene=_scene(), venue=VENUE, history=(),
                utterance=Utterance('hi there', 'en', 0.95), language='en',
                child=False)
    call.update(overrides)
    return backend.respond(**call)


def test_a_good_response_becomes_a_validated_turn():
    client = _StubClient(_ok_response())
    turn = _respond(_backend(client))
    assert turn.reply == 'Nice to meet you!'
    assert turn.gesture == 'wave'
    assert turn.emoji == 'happy'
    assert turn.end is False


def test_both_frames_are_sent_in_one_call():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    blocks = client.request['messages'][0]['content']
    images = [b for b in blocks if b['type'] == 'image']
    assert len(images) == 2, (
        'the wide base frame and the fresh head frame go together: that pair '
        'is what lets the model say "the shelf behind you" without the robot '
        'remembering anything between turns')


def test_the_request_uses_the_opus_5_shape():
    client = _StubClient(_ok_response())
    _respond(_backend(client, model='claude-opus-5', effort='low', timeout_s=6.0))
    request = client.request
    assert request['model'] == 'claude-opus-5'
    assert request['thinking'] == {'type': 'adaptive'}
    assert 'budget_tokens' not in json.dumps(request), (
        'Opus 5 rejects budget_tokens with a 400')
    assert request['output_config']['effort'] == 'low'
    assert request['output_config']['format']['type'] == 'json_schema'
    assert client.options == {'timeout': 6.0, 'max_retries': 0}


def test_the_schema_sent_carries_only_the_enabled_names():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    schema = client.request['output_config']['format']['schema']
    assert set(schema['properties']['gesture']['enum']) == set(GESTURES) | {None}
    assert set(schema['properties']['emoji']['enum']) == set(EMOJI) | {None}


def test_the_venue_facts_and_forbidden_topics_reach_the_prompt():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert 'The fitting rooms are at the back.' in text
    assert 'prices' in text


def test_the_conversation_history_reaches_the_prompt_in_order():
    client = _StubClient(_ok_response())
    _respond(_backend(client), history=(Exchange('person', 'do you have hats'),
                                        Exchange('robot', 'we do')))
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert text.index('do you have hats') < text.index('we do')


def test_child_mode_puts_the_child_rules_in_the_prompt():
    client = _StubClient(_ok_response())
    _respond(_backend(client), child=True)
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    lowered = text.lower()
    assert 'closer' in lowered, (
        'the never-invite-them-closer rule must be stated in the prompt, not '
        'only relied on downstream: the 1.0 m interlock silently cancels the '
        'gesture the robot just invited a child to come and see')
    assert 'promise' in lowered


def test_group_mode_is_stated_so_the_model_stops_saying_you():
    client = _StubClient(_ok_response())
    scene = _scene(distance=3.0, count=4)
    object.__setattr__(scene, 'mode', AddressingMode.GROUP)
    _respond(_backend(client), scene=scene)
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert 'group' in text.lower()


def test_a_response_with_no_text_block_is_unavailable_not_a_crash():
    client = _StubClient(_Response([_Block('thinking')]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_a_non_json_text_block_is_unavailable():
    client = _StubClient(_Response([_Block('text', 'sorry, I cannot do that')]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_a_json_object_with_no_reply_is_unavailable():
    client = _StubClient(_Response([_Block('text', json.dumps({'end': True}))]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_every_sdk_failure_arrives_as_backend_unavailable():
    # The names are checked against the installed SDK so a rename cannot
    # quietly turn a handled failure into an unhandled one.
    import anthropic

    for exc in [
        anthropic.APITimeoutError(request=None),
        anthropic.APIConnectionError(request=None),
        RuntimeError('something else entirely'),
    ]:
        with pytest.raises(BackendUnavailable):
            _respond(_backend(_StubClient(raises=exc)))


def test_no_image_bytes_appear_in_the_exception_or_the_log(caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    client = _StubClient(raises=RuntimeError('boom'))
    backend = _backend(client, logger=logging.getLogger('x2'))
    with pytest.raises(BackendUnavailable) as exc:
        _respond(backend, frame=_Frame(b'\xca\xfe\xba\xbe' * 64))
    joined = str(exc.value) + ' '.join(r.getMessage() for r in caplog.records)
    assert 'cafe' not in joined.lower()
    assert 'yv6' not in joined  # a fragment of the base64 of that payload


def test_a_long_reply_comes_back_truncated():
    client = _StubClient(_ok_response(reply='A sentence. ' * 40))
    turn = _respond(_backend(client, limits=TurnLimits(reply_max_chars=200)))
    assert len(turn.reply) <= 200
