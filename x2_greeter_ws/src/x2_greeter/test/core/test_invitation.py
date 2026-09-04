"""Appending the wake-word invitation to a greeting."""
import random

from x2_greeter.core.invitation import DEFAULT_INVITATIONS, append_invitation


def test_an_invitation_is_appended_to_the_greeting():
    out = append_invitation('Hello there!', ['Say Hi Lumi and I will be happy to chat.'])
    assert out == 'Hello there! Say Hi Lumi and I will be happy to chat.'


def test_one_invitation_is_chosen_from_the_list():
    choices = ['A one.', 'B two.', 'C three.']
    seen = {append_invitation('Hi.', choices, random.Random(seed)).split('Hi. ')[1]
            for seed in range(30)}
    assert seen <= set(choices)
    assert len(seen) > 1, 'the same invitation every time is not a choice'


def test_an_empty_list_leaves_the_greeting_alone():
    # The switch-off: a robot whose interaction can be reached another way
    # should not be telling people to say a wake word.
    assert append_invitation('Hello there!', []) == 'Hello there!'


def test_blank_entries_are_ignored_rather_than_spoken():
    assert append_invitation('Hi.', ['', '   ']) == 'Hi.'


def test_a_blank_greeting_stays_blank():
    # SpeechDispatcher refuses an empty greeting, and turning "nothing to say"
    # into "say Hi Lumi" would have the robot address somebody the cloud has
    # just decided is not there.
    assert append_invitation('', DEFAULT_INVITATIONS) == ''
    assert append_invitation('   ', DEFAULT_INVITATIONS) == ''


def test_the_greeting_is_stripped_before_joining():
    assert append_invitation('  Hello.  ', ['Say Hi Lumi.']) == 'Hello. Say Hi Lumi.'


def test_every_shipped_invitation_names_the_wake_word():
    for phrase in DEFAULT_INVITATIONS:
        assert 'Hi Lumi' in phrase, phrase


def test_the_shipped_invitations_are_speakable():
    # Read aloud by a TTS engine, which reads punctuation literally.
    for phrase in DEFAULT_INVITATIONS:
        assert not any(c in phrase for c in '()[]{}*_#<>'), phrase
        assert phrase.endswith('.'), phrase
