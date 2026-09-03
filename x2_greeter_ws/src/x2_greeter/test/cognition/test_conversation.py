"""The conversation state machine: when the robot talks, and when it stops.

Every number in ConversationLimits is a decision about how a machine
behaves towards a person standing in front of it, so each one gets a test
that would fail if somebody widened it. The session cap is not a resource
limit -- it is the promise that the robot lets go.
"""
import pytest

from x2_greeter.cognition.dialogue import Exchange, Turn
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.cognition.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import parse_venue

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


def _person(distance=1.4, offset=0.0, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=offset, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL, at_s=0.0):
    people = people or (_person(),)
    subject = people[0] if mode is AddressingMode.INDIVIDUAL else None
    return SceneSnapshot(people=people, subject=subject, mode=mode, at_s=at_s)


def _empty_scene(at_s=0.0):
    return SceneSnapshot(people=(), subject=None,
                         mode=AddressingMode.GROUP, at_s=at_s)


def _turn(reply='Sure.', language='en', end=False):
    return Turn(reply=reply, language=language, gesture=None, emoji=None,
                end=end)


def _started(limits=None, at_s=0.0, scene=None):
    convo = Conversation(venue=VENUE, limits=limits or ConversationLimits())
    convo.start(scene if scene is not None else _scene(), at_s=at_s)
    return convo


def _through_one_turn(convo, t0=1.0, turn=None):
    """greeted -> heard -> replied -> finished_speaking, in order."""
    convo.greeted(at_s=t0)
    convo.heard(Utterance('hello', 'en', 0.9), at_s=t0 + 1)
    convo.replied(turn or _turn(), at_s=t0 + 2)
    convo.finished_speaking(at_s=t0 + 3)
    return convo


# --- opening --------------------------------------------------------------

def test_a_fresh_conversation_is_idle():
    convo = Conversation(venue=VENUE)
    assert convo.state is SessionState.IDLE
    assert convo.active is False
    assert convo.turns_taken == 0


def test_start_returns_the_venue_opening_and_enters_greeting():
    convo = Conversation(venue=VENUE)
    assert convo.start(_scene(), at_s=0.0) == 'Welcome in.'
    assert convo.state is SessionState.GREETING


def test_the_opening_is_in_the_venue_default_language():
    convo = Conversation(venue=VENUE)
    convo.start(_scene(), at_s=0.0)
    assert convo.language == 'en'


def test_starting_twice_is_refused():
    convo = _started()
    with pytest.raises(RuntimeError):
        convo.start(_scene(), at_s=1.0)


# --- addressing is locked -------------------------------------------------

def test_addressing_is_decided_once_at_the_start():
    convo = _started(scene=_scene(_person(1.2), _person(1.6)))
    assert convo.mode is AddressingMode.INDIVIDUAL
    assert convo.subject is not None


def test_a_crowd_arriving_mid_conversation_does_not_change_the_mode():
    # 只要交互一开始就不需要改变策略. Switching from 'you' to 'everyone' halfway
    # through reads as broken, not as attentive.
    convo = _through_one_turn(_started(scene=_scene(_person(1.2))))
    convo.observed(_scene(*[_person(3.5)] * 6, mode=AddressingMode.GROUP),
                   at_s=10.0)
    assert convo.mode is AddressingMode.INDIVIDUAL


def test_group_mode_locks_just_as_hard():
    convo = _started(scene=_scene(*[_person(3.2)] * 4,
                                  mode=AddressingMode.GROUP))
    assert convo.mode is AddressingMode.GROUP
    convo.observed(_scene(_person(1.0)), at_s=5.0)
    assert convo.mode is AddressingMode.GROUP


# --- the turn cycle -------------------------------------------------------

def test_the_states_walk_greeting_listening_thinking_speaking_listening():
    convo = _started()
    convo.greeted(at_s=1.0)
    assert convo.state is SessionState.LISTENING
    convo.heard(Utterance('hello', 'en', 0.9), at_s=2.0)
    assert convo.state is SessionState.THINKING
    convo.replied(_turn(), at_s=3.0)
    assert convo.state is SessionState.SPEAKING
    convo.finished_speaking(at_s=4.0)
    assert convo.state is SessionState.LISTENING


def test_a_completed_turn_is_counted_once():
    convo = _through_one_turn(_started())
    assert convo.turns_taken == 1


def test_history_records_both_sides_in_order():
    convo = _through_one_turn(_started())
    assert convo.history == (Exchange('person', 'hello'),
                             Exchange('robot', 'Sure.'))


def test_history_is_trimmed_to_the_limit_keeping_the_most_recent():
    convo = _started(limits=ConversationLimits(history_max=4, max_turns=99))
    for i in range(6):
        convo.greeted(at_s=0.0) if i == 0 else None
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        convo.replied(_turn(reply=f'a{i}'), at_s=10.5 + i)
        convo.finished_speaking(at_s=11.0 + i)
    assert len(convo.history) == 4
    assert convo.history[-1].text == 'a5'


def test_hearing_out_of_turn_is_ignored_not_a_crash():
    # A late transcription can land while the robot is already speaking.
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hello', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(), at_s=3.0)
    convo.heard(Utterance('and another thing', 'en', 0.9), at_s=3.5)
    assert convo.state is SessionState.SPEAKING
    assert [e.text for e in convo.history] == ['hello', 'Sure.']


def test_an_empty_utterance_does_not_start_a_turn():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('   ', 'en', 0.0), at_s=2.0)
    assert convo.state is SessionState.LISTENING
    assert convo.history == ()


# --- language -------------------------------------------------------------

def test_a_confident_language_switch_is_followed():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('你好', 'zh', 0.95), at_s=2.0)
    assert convo.language == 'zh'


def test_an_unconfident_detection_does_not_flip_the_language():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hello 你好', 'zh', 0.3), at_s=2.0)
    assert convo.language == 'en'


def test_the_language_the_model_replied_in_becomes_the_current_language():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('你好', 'zh', 0.95), at_s=2.0)
    convo.replied(_turn(reply='你好！', language='zh'), at_s=3.0)
    assert convo.language == 'zh'


# --- closing --------------------------------------------------------------

def test_the_model_ending_the_turn_closes_after_the_speech_finishes():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('bye', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(reply='See you!', end=True), at_s=3.0)
    assert convo.state is SessionState.SPEAKING, (
        'the goodbye still has to be spoken before the session ends')
    convo.finished_speaking(at_s=4.0)
    assert convo.state is SessionState.COOLDOWN
    assert convo.close_reason is CloseReason.MODEL_ENDED


def test_the_turn_cap_closes_the_session():
    convo = _started(limits=ConversationLimits(max_turns=2))
    convo.greeted(at_s=1.0)
    for i in range(2):
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        convo.replied(_turn(), at_s=10.5 + i)
        convo.finished_speaking(at_s=11.0 + i)
    assert convo.state is SessionState.COOLDOWN
    assert convo.close_reason is CloseReason.MAX_TURNS


def test_silence_while_listening_closes_the_session():
    convo = _through_one_turn(_started(limits=ConversationLimits(
        silence_timeout_s=8.0)))
    convo.tick(at_s=4.0 + 7.9)
    assert convo.state is SessionState.LISTENING
    convo.tick(at_s=4.0 + 8.1)
    assert convo.close_reason is CloseReason.SILENCE


def test_silence_is_measured_from_the_last_thing_that_happened():
    convo = _started(limits=ConversationLimits(silence_timeout_s=8.0))
    convo.greeted(at_s=1.0)
    convo.tick(at_s=8.5)
    assert convo.state is SessionState.LISTENING, 'the clock restarts on each event'
    convo.heard(Utterance('hi', 'en', 0.9), at_s=8.6)
    convo.replied(_turn(), at_s=8.7)
    convo.finished_speaking(at_s=8.8)
    convo.tick(at_s=16.0)
    assert convo.state is SessionState.LISTENING
    convo.tick(at_s=17.0)
    assert convo.close_reason is CloseReason.SILENCE


def test_the_thinking_state_is_not_cut_short_by_the_silence_timeout():
    # A slow backend is not the person going quiet.
    convo = _started(limits=ConversationLimits(silence_timeout_s=1.0))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    convo.tick(at_s=30.0)
    assert convo.state is SessionState.THINKING


def test_the_session_cap_closes_even_a_lively_conversation():
    limits = ConversationLimits(session_max_s=180.0, max_turns=99,
                                silence_timeout_s=999.0)
    convo = _started(limits=limits, at_s=100.0)
    convo.greeted(at_s=101.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=270.0)
    convo.tick(at_s=279.9)
    assert convo.state is SessionState.THINKING
    convo.tick(at_s=280.1)
    assert convo.close_reason is CloseReason.SESSION_TIMEOUT, (
        'the cap is a promise that the robot lets go, not a resource limit')


def test_the_person_walking_away_closes_the_session():
    convo = _through_one_turn(_started())
    convo.observed(_empty_scene(at_s=10.0), at_s=10.0)
    assert convo.close_reason is CloseReason.PERSON_GONE


def test_an_empty_scene_while_speaking_does_not_cut_the_sentence():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(), at_s=3.0)
    convo.observed(_empty_scene(at_s=3.5), at_s=3.5)
    assert convo.state is SessionState.SPEAKING
    convo.finished_speaking(at_s=4.0)
    assert convo.close_reason is CloseReason.PERSON_GONE


def test_close_can_be_called_directly_and_is_idempotent():
    convo = _through_one_turn(_started())
    convo.close(CloseReason.ABORTED, at_s=10.0)
    convo.close(CloseReason.SILENCE, at_s=11.0)
    assert convo.close_reason is CloseReason.ABORTED, 'the first reason wins'


# --- backend failures -----------------------------------------------------

def test_one_backend_failure_returns_to_listening():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    assert convo.backend_failed(at_s=3.0) is False
    assert convo.state is SessionState.LISTENING


def test_the_failure_budget_closes_the_session_when_it_runs_out():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    for i in range(2):
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        fatal = convo.backend_failed(at_s=10.5 + i)
    assert fatal is True
    assert convo.close_reason is CloseReason.BACKEND_FAILED


def test_a_successful_turn_forgives_the_earlier_failures():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('q', 'en', 0.9), at_s=2.0)
    convo.backend_failed(at_s=3.0)
    convo.heard(Utterance('q again', 'en', 0.9), at_s=4.0)
    convo.replied(_turn(), at_s=5.0)
    convo.finished_speaking(at_s=6.0)
    convo.heard(Utterance('q3', 'en', 0.9), at_s=7.0)
    assert convo.backend_failed(at_s=8.0) is False, (
        'a working turn means the network came back; the budget resets')


# --- cooldown -------------------------------------------------------------

def test_cooldown_runs_from_the_close_and_then_expires():
    convo = _through_one_turn(_started(limits=ConversationLimits(
        cooldown_s=20.0)))
    convo.close(CloseReason.SILENCE, at_s=10.0)
    assert convo.cooldown_active(at_s=29.9) is True
    assert convo.cooldown_active(at_s=30.1) is False


def test_a_conversation_that_never_started_is_not_in_cooldown():
    assert Conversation(venue=VENUE).cooldown_active(at_s=0.0) is False


def test_reset_returns_to_idle_and_clears_the_session():
    convo = _through_one_turn(_started())
    convo.close(CloseReason.SILENCE, at_s=10.0)
    convo.reset()
    assert convo.state is SessionState.IDLE
    assert convo.turns_taken == 0
    assert convo.history == ()
    assert convo.close_reason is None
    assert convo.subject is None
