"""Every catalogued motion id must exist in the vendor's own enum.

This is the test that was missing. `core/gestures.py` once carried 1007 for
the heart gesture, 1010 for raise-both and 1011 for a chest wave, none of
which are members of McPresetMotion -- and `test_gestures.py` pinned those
numbers, because it was written from the same wrong source. The controller
refused all three silently, so roughly one greeting in four produced no
gesture at all.

A table of expected ids can only ever agree with whoever typed it. This
compares against `aimdk_msgs.msg.McPresetMotion`, which is why it lives here,
in the suite that has the vendor messages, rather than beside the others.
"""
import pytest

pytestmark = pytest.mark.ros


def _vendor_motion_ids():
    from aimdk_msgs.msg import McPresetMotion
    return {
        value: name
        for name, value in vars(McPresetMotion).items()
        if isinstance(value, int) and name.isupper()
    }


def test_every_catalogued_motion_id_is_a_real_vendor_motion():
    from x2_greeter.core.gestures import CATALOGUE

    known = _vendor_motion_ids()
    invented = {
        name: spec.motion_id
        for name, spec in CATALOGUE.items()
        if spec.motion_id not in known
    }
    assert not invented, (
        f'gesture(s) with motion ids that McPresetMotion does not define: '
        f'{invented}. The controller refuses these and says nothing useful; '
        f'take the id from the vendor .msg, not from a description of it.')


def test_the_default_enabled_gestures_are_all_real():
    # The subset that actually ships enabled, called out separately so a
    # failure names the ones a live robot would have tried to perform.
    from x2_greeter.core.gestures import CATALOGUE, DEFAULT_ENABLED

    known = _vendor_motion_ids()
    broken = [name for name in DEFAULT_ENABLED
              if CATALOGUE[name].motion_id not in known]
    assert not broken, f'shipped-enabled gestures with invalid motion ids: {broken}'


def test_the_heart_gesture_is_the_vendor_sweatheart_motion():
    # Named explicitly because this is the one that was wrong, and because
    # "heart" is ambiguous enough that the next person deserves the mapping
    # spelled out: INTERACTION_SWEATHEART, hands above the head.
    from aimdk_msgs.msg import McPresetMotion

    from x2_greeter.core.gestures import CATALOGUE

    assert CATALOGUE['heart'].motion_id == McPresetMotion.INTERACTION_SWEATHEART
