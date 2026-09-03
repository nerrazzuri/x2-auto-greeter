"""The turn contract: what comes back from a dialogue backend, and what
survives validation.

Every rule here exists because a language model will eventually break it. The
model is asked for a gesture name from an allowlist; it will invent one. It
is asked for 'en' or 'zh'; it will answer 'English'. It is told to keep the
reply short; it will write a paragraph. None of those are errors worth
aborting a conversation over -- they are fields to drop or repair -- but a
reply that is missing or empty is, because a robot that gestures at somebody
in silence is worse than one that says nothing at all.
"""
import pytest

from x2_greeter.cognition.dialogue import (
    Exchange, Turn, TurnLimits, TurnRejected, build_turn_schema,
    truncate_at_sentence, validate_turn)
from x2_greeter.sim.fakes import ScriptedDialogueBackend

GESTURES = ('wave', 'heart', 'bow')
EMOJI = ('thinking', 'happy', 'confused')
LIMITS = TurnLimits(reply_max_chars=200)


def _valid(**overrides):
    doc = {'reply': 'Hello there!', 'language': 'en', 'gesture': 'wave',
           'emoji': 'happy', 'end': False}
    doc.update(overrides)
    return doc


def test_a_well_formed_turn_passes_through_unchanged():
    turn = validate_turn(_valid(), GESTURES, EMOJI, LIMITS, 'en')
    assert turn == Turn(reply='Hello there!', language='en', gesture='wave',
                        emoji='happy', end=False)


@pytest.mark.parametrize('doc', [
    {}, {'reply': ''}, {'reply': '   '}, {'reply': None}, {'reply': 42},
    'not a dict', None, [],
])
def test_a_turn_with_no_usable_reply_is_rejected(doc):
    with pytest.raises(TurnRejected):
        validate_turn(doc, GESTURES, EMOJI, LIMITS, 'en')


def test_a_gesture_that_is_not_enabled_is_dropped_not_rejected():
    turn = validate_turn(_valid(gesture='backflip'), GESTURES, EMOJI, LIMITS, 'en')
    assert turn.gesture is None
    assert turn.reply == 'Hello there!', 'the words survive a bad gesture'


def test_an_emoji_that_is_not_enabled_is_dropped():
    assert validate_turn(_valid(emoji='rage'), GESTURES, EMOJI, LIMITS,
                         'en').emoji is None


def test_a_null_gesture_and_emoji_are_perfectly_valid():
    turn = validate_turn(_valid(gesture=None, emoji=None), GESTURES, EMOJI,
                         LIMITS, 'en')
    assert turn.gesture is None and turn.emoji is None


def test_a_language_the_phase_does_not_speak_falls_back():
    # 'ms' is deferred; 'English' is the model answering in prose. Neither is
    # a reason to drop the reply.
    for bad in ['ms', 'English', '', None, 'fr']:
        turn = validate_turn(_valid(language=bad), GESTURES, EMOJI, LIMITS, 'zh')
        assert turn.language == 'zh'


def test_a_regional_language_tag_is_normalised():
    assert validate_turn(_valid(language='zh-CN'), GESTURES, EMOJI, LIMITS,
                         'en').language == 'zh'


@pytest.mark.parametrize('raw, expected', [
    (True, True), (False, False), ('yes', False), (1, False), (None, False)])
def test_end_is_only_true_for_a_real_boolean(raw, expected):
    # Ending the conversation is a decision; a truthy string is not one.
    assert validate_turn(_valid(end=raw), GESTURES, EMOJI, LIMITS,
                         'en').end is expected


def test_a_long_reply_is_truncated_at_the_last_sentence_that_fits():
    long_reply = ('That is a great question. ' * 20).strip()
    turn = validate_turn(_valid(reply=long_reply), GESTURES, EMOJI, LIMITS, 'en')
    assert len(turn.reply) <= 200
    assert turn.reply.endswith('.')


def test_truncation_prefers_a_sentence_boundary():
    text = 'One. Two. Three is a much longer sentence that will not fit.'
    assert truncate_at_sentence(text, 20) == 'One. Two.'


@pytest.mark.parametrize('terminator', ['.', '!', '?', '。', '！', '？'])
def test_truncation_understands_both_scripts(terminator):
    text = f'First{terminator} Second sentence goes well past the limit here.'
    assert truncate_at_sentence(text, 12) == f'First{terminator}'


def test_truncation_falls_back_to_a_word_boundary_when_there_is_no_sentence():
    text = 'a rambling reply with no punctuation at all anywhere in it'
    result = truncate_at_sentence(text, 20)
    assert len(result) <= 20
    assert not result.endswith(' ')
    assert 'rambl' in result


def test_truncation_leaves_a_short_reply_alone():
    assert truncate_at_sentence('Hi there.', 200) == 'Hi there.'


def test_the_schema_offers_only_the_enabled_names():
    schema = build_turn_schema(GESTURES, EMOJI)
    props = schema['properties']
    assert set(props) == {'reply', 'language', 'gesture', 'emoji', 'end'}
    assert schema['required'] == ['reply', 'language', 'gesture', 'emoji', 'end']
    assert schema['additionalProperties'] is False
    assert set(props['gesture']['enum']) == set(GESTURES) | {None}
    assert set(props['emoji']['enum']) == set(EMOJI) | {None}
    assert props['language']['enum'] == ['en', 'zh']
    assert props['end']['type'] == 'boolean'


def test_the_schema_still_permits_no_gesture_when_none_are_enabled():
    # A deployment can disable every gesture. The schema must stay valid.
    schema = build_turn_schema((), ())
    assert schema['properties']['gesture']['enum'] == [None]
    assert schema['properties']['emoji']['enum'] == [None]


def test_an_exchange_records_who_said_it():
    assert Exchange('person', 'hello').speaker == 'person'
    assert Exchange('robot', 'hi').text == 'hi'


def test_the_scripted_backend_returns_prepared_turns_and_records_its_input():
    turns = [Turn('one', 'en', None, None, False),
             Turn('two', 'en', 'wave', 'happy', True)]
    backend = ScriptedDialogueBackend(turns)
    first = backend.respond(base_frame=None, frame=None, scene=None,
                            venue=None, history=(), utterance=None,
                            language='en', child=False)
    assert first.reply == 'one'
    assert backend.calls == 1
    assert backend.last_call['language'] == 'en'
    assert backend.respond(None, None, None, None, (), None, 'en', False).end is True


def test_the_scripted_backend_can_be_made_to_fail_on_a_chosen_turn():
    from x2_greeter.cognition.dialogue import BackendUnavailable

    backend = ScriptedDialogueBackend([Turn('one', 'en', None, None, False)],
                                      fail_after=1)
    backend.respond(None, None, None, None, (), None, 'en', False)
    with pytest.raises(BackendUnavailable):
        backend.respond(None, None, None, None, (), None, 'en', False)
