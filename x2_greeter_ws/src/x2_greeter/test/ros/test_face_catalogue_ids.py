"""Every catalogued emotion id must be a constant on the vendor's PlayEmoji.

The design doc said emoji ids exist only in a documentation table. They do
not: aimdk_msgs/srv/PlayEmoji.srv declares the whole enum as service
constants, so the same pin that protects the gesture catalogue protects this
one. Checking against the message is always better than checking against a
description of the message -- that is the lesson this project keeps paying
for.
"""
import pytest

from x2_greeter.core.faces import CATALOGUE, MODE_LOOP, MODE_ONCE

pytestmark = pytest.mark.ros


def _vendor_emotion_ids():
    from aimdk_msgs.srv import PlayEmoji
    return {value: name for name, value in vars(PlayEmoji).items()
            if isinstance(value, int) and name.startswith('EMOTION_')}


def test_every_catalogued_emotion_id_is_a_vendor_constant():
    known = _vendor_emotion_ids()
    invented = {name: spec.emotion_id for name, spec in CATALOGUE.items()
                if spec.emotion_id not in known}
    assert not invented, (
        f'emoji with ids PlayEmoji does not define: {invented}. Take the id '
        f'from the .srv, not from a table describing it.')


def test_the_thinking_emoji_is_the_vendor_thinking_eye():
    from aimdk_msgs.srv import PlayEmoji

    assert CATALOGUE['thinking'].emotion_id == PlayEmoji.EMOTION_EYE_THINKING


def test_the_modes_match_the_vendor_constants():
    from aimdk_msgs.srv import PlayEmoji

    assert MODE_ONCE == PlayEmoji.EMOTION_MODE_ONCE
    assert MODE_LOOP == PlayEmoji.EMOTION_MODE_LOOP
