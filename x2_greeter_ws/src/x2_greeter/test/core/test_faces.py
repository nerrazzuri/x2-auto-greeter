"""The emoji catalogue and the allowlist the model picks from.

Same shape as core/gestures.py and for the same reason: the model supplies a
*name*, which is validated against the enabled allowlist before it ever
becomes a vendor id. An emoji is the cheapest thing the robot can do -- it is
the only thing that happens between the person finishing their sentence and
the reply arriving four to eight seconds later -- so the catalogue ships
enabled, unlike the head.
"""
import pytest

from x2_greeter.core.faces import (
    CATALOGUE, DEFAULT_ENABLED, MODE_LOOP, MODE_ONCE, THINKING, EmojiSelector)


def test_the_thinking_emoji_is_in_the_catalogue_and_enabled():
    # Spec section 13: this is the only thing standing between the person and
    # several seconds of a robot that looks switched off.
    assert THINKING in CATALOGUE
    assert THINKING in DEFAULT_ENABLED


def test_the_modes_are_the_vendor_modes():
    assert MODE_ONCE == 1
    assert MODE_LOOP == 2


def test_every_default_enabled_emoji_is_in_the_catalogue():
    unknown = [name for name in DEFAULT_ENABLED if name not in CATALOGUE]
    assert not unknown, unknown


def test_no_angry_emoji_is_catalogued_at_all():
    # The vendor offers ANGRY and EXTREMEANGRY. A greeter has no use for
    # either, and the cheapest way to guarantee the model never picks one is
    # for the name not to exist.
    assert not [name for name in CATALOGUE if 'angry' in name]


def test_nothing_is_marked_hardware_verified_yet():
    # Nobody has watched the face screen. The flag records observation.
    assert not [name for name, spec in CATALOGUE.items() if spec.hw_verified]


def test_the_selector_honours_a_name_on_the_allowlist():
    selector = EmojiSelector(['thinking', 'happy'])
    assert selector.select('happy').name == 'happy'
    assert selector.select('happy').emotion_id == CATALOGUE['happy'].emotion_id


def test_the_selector_refuses_a_name_that_is_not_enabled():
    # Not a fallback to something random: an emoji nobody asked for is worse
    # than no emoji, and a disabled name means somebody disabled it.
    selector = EmojiSelector(['thinking', 'happy'])
    assert selector.select('shock') is None
    assert selector.select('nonsense') is None
    assert selector.select(None) is None


def test_the_selector_exposes_the_thinking_emoji_when_it_is_enabled():
    assert EmojiSelector(['thinking', 'happy']).thinking.name == 'thinking'


def test_the_selector_reports_no_thinking_emoji_when_it_is_disabled():
    # A deployment may turn the whole face off. That must degrade to silence,
    # not to an exception on the latency path.
    assert EmojiSelector(['happy']).thinking is None


def test_an_empty_allowlist_disables_the_face_rather_than_failing():
    selector = EmojiSelector([])
    assert selector.enabled_names == ()
    assert selector.thinking is None
    assert selector.select('happy') is None


def test_an_unknown_name_in_the_allowlist_is_a_configuration_error():
    with pytest.raises(ValueError) as exc:
        EmojiSelector(['happy', 'grumpy'])
    assert 'grumpy' in str(exc.value)
