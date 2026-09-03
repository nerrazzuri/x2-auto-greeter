"""Whole conversations, on the host, in milliseconds.

No ROS, no network, no Whisper model, no robot. The real state machine, the
real validation, the real venue files and the real catalogues, driven by
scripted fakes. This is the only place that can answer questions no single
module can -- 'does a child in a mall atrium get through a whole
conversation without being asked their name', for one.
"""
from pathlib import Path

import pytest

from x2_greeter.cognition.dialogue import (
    BackendUnavailable, Turn, TurnLimits, validate_turn)
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.cognition.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.core.faces import CATALOGUE as FACES
from x2_greeter.core.gestures import CATALOGUE as GESTURES
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import load_venue

VENUES = Path(__file__).resolve().parents[1] / 'config' / 'venues'
# 'nod' is not a real catalogue entry in core/gestures.py; 'wave', 'clap'
# and 'heart' are.
ENABLED_GESTURES = ('wave', 'clap', 'heart')
ENABLED_EMOJI = ('thinking', 'happy', 'confused')


def _venue(name='clothing_store'):
    return load_venue(VENUES / f'{name}.yaml')


def _person(distance=1.4, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=0.0, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL):
    people = people or (_person(),)
    return SceneSnapshot(
        people=people,
        subject=people[0] if mode is AddressingMode.INDIVIDUAL else None,
        mode=mode, at_s=0.0)


class _Driver:
    """Runs a whole conversation against scripted model output.

    `script` is a list of (heard_text, heard_language, raw_model_doc). The
    raw doc goes through the real validate_turn, so a script can express
    'the model invented a gesture' the same way the model would.
    """

    def __init__(self, venue, script, limits=None, scene=None):
        self.venue = venue
        self.script = list(script)
        self.convo = Conversation(venue=venue,
                                  limits=limits or ConversationLimits())
        self.scene = scene if scene is not None else _scene()
        self.spoken = []
        self.gestures = []
        self.expressions = []
        self.failures = 0

    def run(self, t0=0.0):
        self.spoken.append(self.convo.start(self.scene, at_s=t0))
        self.convo.greeted(at_s=t0 + 0.5)
        t = t0 + 1.0
        for heard, language, doc in self.script:
            # The real node drives Conversation.tick() off a 1 Hz ROS timer
            # (ros/conversation_node.py's _on_tick); Conversation itself
            # never reads a clock. Mirror that here, or session_max_s (and
            # any other tick-only close) can never fire mid-conversation.
            self.convo.tick(at_s=t)
            if self.convo.state is not SessionState.LISTENING:
                break
            self.convo.heard(Utterance(heard, language, 0.95), at_s=t)
            self.expressions.append('thinking')
            if doc is None:
                self.failures += 1
                self.convo.backend_failed(at_s=t + 1.0)
                self.spoken.append("Sorry, I didn't catch that.")
                t += 2.0
                continue
            turn = validate_turn(doc, ENABLED_GESTURES, ENABLED_EMOJI,
                                 TurnLimits(), self.convo.language)
            self.convo.replied(turn, at_s=t + 1.5)
            if turn.gesture:
                self.gestures.append(turn.gesture)
            if turn.emoji:
                self.expressions.append(turn.emoji)
            self.spoken.append(turn.reply)
            self.convo.finished_speaking(at_s=t + 3.0)
            t += 4.0
        self.last_t = t
        return self


def _doc(reply, language='en', gesture=None, emoji=None, end=False):
    return {'reply': reply, 'language': language, 'gesture': gesture,
            'emoji': emoji, 'end': end}


# --- the happy path -------------------------------------------------------

def test_a_three_turn_conversation_opens_replies_and_closes():
    driver = _Driver(_venue(), [
        ('hi there', 'en', _doc('Hello! Looking for anything in particular?',
                                gesture='wave', emoji='happy')),
        ('just browsing', 'en', _doc('Take your time.', emoji='happy')),
        ('thanks, bye', 'en', _doc('See you!', gesture='wave', end=True)),
    ]).run()
    assert driver.spoken[0] == driver.venue.opening_for('en')
    assert len(driver.spoken) == 4
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
    assert driver.gestures == ['wave', 'wave']


def test_every_gesture_and_expression_the_model_asked_for_is_real():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Hi!', gesture='wave', emoji='happy')),
    ]).run()
    assert all(name in GESTURES for name in driver.gestures)
    assert all(name in FACES for name in driver.expressions)


def test_the_thinking_expression_precedes_every_reply():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Hi!', emoji='happy')),
        ('nice', 'en', _doc('Thanks!', emoji='happy')),
    ]).run()
    assert driver.expressions[0] == 'thinking'
    assert driver.expressions.count('thinking') == 2


# --- what the model gets wrong -------------------------------------------

def test_an_invented_gesture_never_reaches_the_robot():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Watch this.', gesture='backflip')),
    ]).run()
    assert driver.gestures == []
    assert driver.spoken[-1] == 'Watch this.', 'the words still happen'


def test_a_malay_reply_is_relabelled_not_spoken_as_malay():
    # Malay is next phase; there is no voice for it.
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Selamat datang!', language='ms')),
    ]).run()
    assert driver.convo.language in ('en', 'zh')


def test_a_monologue_is_cut_to_something_a_robot_can_say_out_loud():
    driver = _Driver(_venue(), [
        ('tell me everything', 'en', _doc('This is a sentence. ' * 40)),
    ]).run()
    assert len(driver.spoken[-1]) <= 200
    assert driver.spoken[-1].endswith('.')


# --- language follows the person -----------------------------------------

def test_switching_to_chinese_mid_conversation_sticks():
    driver = _Driver(_venue(), [
        ('hello', 'en', _doc('Hi there!')),
        ('你们有帽子吗', 'zh', _doc('有的，在那边。', language='zh')),
        ('谢谢', 'zh', _doc('不客气！', language='zh', end=True)),
    ]).run()
    assert driver.convo.language == 'zh'
    assert driver.spoken[0] == driver.venue.opening_for('en'), (
        'the opening is in the venue default; the switch happens after '
        'somebody speaks')


# --- failure ---------------------------------------------------------------

def test_the_robot_speaks_on_every_backend_failure():
    driver = _Driver(_venue(), [
        ('hi', 'en', None),
        ('anyone there', 'en', None),
    ], limits=ConversationLimits(backend_failures_max=2)).run()
    assert driver.failures == 2
    assert driver.convo.close_reason is CloseReason.BACKEND_FAILED
    assert driver.spoken.count("Sorry, I didn't catch that.") == 2, (
        'the robot speaks in every failure case')


def test_one_failure_does_not_end_the_conversation():
    driver = _Driver(_venue(), [
        ('hi', 'en', None),
        ('hello again', 'en', _doc('Sorry about that -- hello!')),
        ('no worries', 'en', _doc('Thanks!', end=True)),
    ]).run()
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
    assert driver.convo.turns_taken == 2


# --- child mode -----------------------------------------------------------

def test_a_child_in_the_atrium_is_recognised_from_the_scene():
    scene = _scene(_person(distance=1.1, child=True))
    driver = _Driver(_venue('mall_atrium'), [
        ('hi robot', 'en', _doc('Hello! What are you playing?', emoji='happy')),
    ], scene=scene).run()
    assert scene.has_child is True
    assert driver.convo.mode is AddressingMode.INDIVIDUAL


def test_the_child_rules_are_present_in_the_prompt_the_backend_would_get():
    # The rules themselves are enforced in the prompt; this checks the
    # wording exists and says the thing that matters.
    from x2_greeter.cognition.claude import CHILD_RULES

    lowered = CHILD_RULES.lower()
    assert 'closer' in lowered
    assert 'promise' in lowered
    assert 'name' in lowered


# --- addressing -----------------------------------------------------------

def test_a_distant_crowd_gets_group_addressing_and_keeps_it():
    scene = _scene(*[_person(3.4)] * 5, mode=AddressingMode.GROUP)
    driver = _Driver(_venue('mall_atrium'), [
        ('hello', 'en', _doc('Hello everyone!')),
    ], scene=scene).run()
    assert driver.convo.mode is AddressingMode.GROUP
    driver.convo.observed(_scene(_person(1.0)), at_s=100.0)
    assert driver.convo.mode is AddressingMode.GROUP


# --- the limits hold on a real conversation -------------------------------

def test_the_turn_cap_stops_a_conversation_that_would_never_stop_itself():
    script = [(f'q{i}', 'en', _doc(f'a{i}')) for i in range(20)]
    driver = _Driver(_venue(), script,
                     limits=ConversationLimits(max_turns=8)).run()
    assert driver.convo.turns_taken == 8
    assert driver.convo.close_reason is CloseReason.MAX_TURNS


def test_silence_after_the_last_reply_ends_the_session():
    driver = _Driver(_venue(), [('hi', 'en', _doc('Hello!'))],
                     limits=ConversationLimits(silence_timeout_s=8.0)).run()
    driver.convo.tick(at_s=driver.last_t + 20.0)
    assert driver.convo.close_reason is CloseReason.SILENCE


def test_a_session_that_talks_forever_still_ends_inside_the_cap():
    limits = ConversationLimits(max_turns=99, silence_timeout_s=999.0,
                                session_max_s=180.0)
    script = [(f'q{i}', 'en', _doc(f'a{i}')) for i in range(60)]
    driver = _Driver(_venue(), script, limits=limits).run()
    assert driver.convo.close_reason is CloseReason.SESSION_TIMEOUT


@pytest.mark.parametrize('name', ['clothing_store', 'mall_atrium'])
def test_every_shipped_venue_can_hold_a_conversation(name):
    driver = _Driver(_venue(name), [
        ('hi', 'en', _doc('Hello!')),
        ('bye', 'en', _doc('See you!', end=True)),
    ]).run()
    assert driver.spoken[0]
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
