"""Appending the wake-word invitation to a greeting."""
import random

from x2_greeter.core.invitation import DEFAULT_INVITATIONS, WAKE_WORD, append_invitation


def test_an_invitation_is_appended_to_the_greeting():
    out = append_invitation('Hello there!', ['Say Lingxi Lingxi and I will be happy to chat.'])
    assert out == 'Hello there! Say Lingxi Lingxi and I will be happy to chat.'


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
    # into an invitation would have the robot address somebody the cloud has
    # just decided is not there.
    assert append_invitation('', DEFAULT_INVITATIONS) == ''
    assert append_invitation('   ', DEFAULT_INVITATIONS) == ''


def test_the_greeting_is_stripped_before_joining():
    assert append_invitation('  Hello.  ', ['Say Lingxi Lingxi.']) == 'Hello. Say Lingxi Lingxi.'


def test_every_shipped_invitation_names_the_wake_word():
    for phrase in DEFAULT_INVITATIONS:
        assert WAKE_WORD in phrase, phrase


def test_the_old_wake_word_is_gone():
    """Hi Lumi opens nothing on these robots any more. A visitor told to say
    it says it, and walks away thinking the robot is broken."""
    for phrase in DEFAULT_INVITATIONS:
        assert 'lumi' not in phrase.lower(), phrase


def test_the_shipped_invitations_are_speakable():
    # Read aloud by a TTS engine, which reads punctuation literally.
    for phrase in DEFAULT_INVITATIONS:
        assert not any(c in phrase for c in '()[]{}*_#<>'), phrase
        assert phrase.endswith('.'), phrase
