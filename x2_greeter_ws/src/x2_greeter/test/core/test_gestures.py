import random

import pytest

from x2_greeter.core.gestures import (
    AREA_BOTH,
    AREA_LEFT,
    AREA_RIGHT,
    AREA_WHOLE_BODY,
    CATALOGUE,
    DEFAULT_ENABLED,
    GestureSelector,
    resolve_area,
)

EXPECTED_ENABLED = {
    'wave', 'salute', 'handshake', 'raise_hand', 'raise_both', 'bow',
    'high_five', 'wave_chest', 'cheer', 'blow_kiss', 'heart',
}
EXPECTED_DISABLED = {
    'hug', 'clap', 'cross_arms', 'dynamic_light_wave',
    'like', 'peace', 'fist_bump', 'turn_wave',
}


def test_default_enabled_matches_the_spec():
    assert set(DEFAULT_ENABLED) == EXPECTED_ENABLED


def test_catalogue_also_carries_the_disabled_gestures():
    assert EXPECTED_DISABLED <= set(CATALOGUE)
    assert set(CATALOGUE) == EXPECTED_ENABLED | EXPECTED_DISABLED


@pytest.mark.parametrize(('name', 'motion_id'), [
    ('wave', 1002), ('salute', 1013), ('handshake', 1003), ('raise_hand', 1001),
    ('raise_both', 1001), ('bow', 3001), ('high_five', 1008), ('wave_chest', 3010),
    ('cheer', 3011), ('blow_kiss', 1004), ('heart', 3004),
    ('hug', 3008), ('clap', 3015), ('cross_arms', 3009),
    ('dynamic_light_wave', 3007), ('like', 3002), ('peace', 3003),
    ('fist_bump', 1009), ('turn_wave', 2001),
])
def test_motion_ids_match_the_interface_docs(name, motion_id):
    assert CATALOGUE[name].motion_id == motion_id


def test_handed_gesture_honours_the_hand_preference():
    wave = CATALOGUE['wave']
    rng = random.Random(0)
    assert resolve_area(wave, 'right', rng) == AREA_RIGHT
    assert resolve_area(wave, 'left', rng) == AREA_LEFT


def test_whole_body_gesture_ignores_the_hand_preference():
    bow = CATALOGUE['bow']
    rng = random.Random(0)
    assert resolve_area(bow, 'left', rng) == AREA_WHOLE_BODY
    assert resolve_area(bow, 'both', rng) == AREA_WHOLE_BODY


def test_both_arm_gesture_ignores_the_hand_preference():
    rng = random.Random(0)
    assert resolve_area(CATALOGUE['raise_both'], 'left', rng) == AREA_BOTH


def test_heart_is_a_whole_body_interaction_motion():
    # 3004 is the vendor's INTERACTION_SWEATHEART, "heart above the head" --
    # both arms, so the hand preference has nothing to choose between. It was
    # previously catalogued as 1007 with per-hand areas; 1007 is not a member
    # of McPresetMotion at all, and the controller refused every one.
    heart = CATALOGUE['heart']
    rng = random.Random(0)
    for preference in ('both', 'left', 'right'):
        assert resolve_area(heart, preference, rng) == AREA_WHOLE_BODY


def test_both_preference_on_a_single_arm_gesture_falls_back_to_the_first_area():
    # 'wave' has no both-arm variant; area 2 (right) is its documented default.
    assert resolve_area(CATALOGUE['wave'], 'both', random.Random(0)) == AREA_RIGHT


def test_random_preference_picks_a_side_per_call():
    wave = CATALOGUE['wave']
    sides = {resolve_area(wave, 'random', random.Random(seed)) for seed in range(20)}
    assert sides == {AREA_LEFT, AREA_RIGHT}


def test_selector_uses_a_requested_gesture_that_is_enabled():
    sel = GestureSelector(['wave', 'salute'], hand_preference='right', rng=random.Random(0))
    choice = sel.select('salute')
    assert choice.name == 'salute'
    assert choice.motion_id == 1013
    assert choice.area_id == AREA_RIGHT


def test_selector_rejects_a_gesture_outside_the_allowlist():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    # 'hug' exists in the catalogue but is not enabled.
    assert sel.select('hug').name in {'wave', 'salute'}


def test_selector_rejects_a_gesture_that_is_not_a_gesture_at_all():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    assert sel.select('DROP TABLE gestures').name in {'wave', 'salute'}


def test_selector_never_repeats_the_previous_gesture():
    sel = GestureSelector(['wave', 'salute', 'bow'], rng=random.Random(7))
    previous = None
    for _ in range(30):
        name = sel.select().name
        assert name != previous
        previous = name


def test_selector_can_repeat_when_only_one_gesture_is_enabled():
    sel = GestureSelector(['wave'], rng=random.Random(0))
    assert [sel.select().name for _ in range(3)] == ['wave', 'wave', 'wave']


def test_a_requested_gesture_also_counts_as_the_previous_one():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    sel.select('wave')
    assert sel.select().name == 'salute'


def test_selector_rejects_an_unknown_name_at_construction():
    with pytest.raises(ValueError, match='not_a_gesture'):
        GestureSelector(['wave', 'not_a_gesture'])


def test_selector_rejects_an_empty_allowlist():
    with pytest.raises(ValueError, match='at least one'):
        GestureSelector([])


def test_selector_rejects_an_unknown_hand_preference():
    with pytest.raises(ValueError, match='hand_preference'):
        GestureSelector(['wave'], hand_preference='sideways')


def test_enabled_names_is_exposed_for_the_llm_schema():
    sel = GestureSelector(['wave', 'salute'])
    assert sel.enabled_names == ('wave', 'salute')


# ------------------------------------------------ narrowing the pool by mode

def test_the_allowed_list_narrows_the_pool():
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave', 'bow', 'salute'], rng=random.Random(0))
    for _ in range(20):
        assert selector.select(allowed=['wave']).name == 'wave'


def test_the_allowed_list_can_never_widen_the_pool():
    """A stale walking list must not resurrect a gesture the operator has
    switched off. `allowed` intersects with `enabled`; it does not replace it.
    """
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave'], rng=random.Random(0))
    for _ in range(20):
        assert selector.select(allowed=['wave', 'bow', 'cheer']).name == 'wave'


def test_a_requested_gesture_outside_the_allowed_list_is_ignored():
    # The cloud may ask for a bow while the robot is walking. It does not get one.
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave', 'bow'], rng=random.Random(0))
    assert selector.select('bow', allowed=['wave']).name == 'wave'


def test_an_empty_intersection_raises_rather_than_choosing_something_else():
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave'], rng=random.Random(0))
    with pytest.raises(ValueError, match='enabled and allowed'):
        selector.select(allowed=['bow'])


# --------------------------------------------------------- the waist marker

def test_arm_gestures_do_not_use_the_waist():
    from x2_greeter.core.gestures import CATALOGUE, uses_waist

    for name in ('wave', 'salute', 'handshake', 'raise_hand', 'raise_both',
                 'high_five', 'blow_kiss', 'fist_bump'):
        assert not uses_waist(CATALOGUE[name]), name


def test_whole_body_gestures_use_the_waist():
    from x2_greeter.core.gestures import CATALOGUE, uses_waist

    for name in ('bow', 'cheer', 'heart', 'wave_chest', 'hug'):
        assert uses_waist(CATALOGUE[name]), name


# ------------------------------------------- a requested repeat is broken up

def test_a_requested_gesture_is_honoured_when_it_is_not_a_repeat():
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave', 'bow', 'salute'], rng=random.Random(0))
    selector.select('bow')
    assert selector.select('salute').name == 'salute'


def test_the_same_requested_gesture_twice_running_is_replaced():
    """The cloud picks a wave for every stranger who walks up, and the
    controller refuses the same preset motion twice in a row -- so honouring
    the repeat costs both the variety and the movement."""
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave', 'bow', 'salute'], rng=random.Random(0))
    assert selector.select('wave').name == 'wave'
    second = selector.select('wave').name
    assert second != 'wave'
    assert second in ('bow', 'salute')


def test_a_repeat_is_still_replaced_from_a_narrowed_pool():
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave', 'bow', 'salute'], rng=random.Random(0))
    selector.select('salute', allowed=['salute', 'wave'])
    assert selector.select('salute', allowed=['salute', 'wave']).name == 'wave'


def test_a_repeat_survives_when_it_is_the_only_gesture_available():
    # With one gesture there is no alternative. The controller will refuse
    # roughly every other one; that is a reason to enable more than one, not
    # a reason for the selector to invent something outside the allowlist.
    from x2_greeter.core.gestures import GestureSelector

    selector = GestureSelector(enabled=['wave'], rng=random.Random(0))
    assert selector.select('wave').name == 'wave'
    assert selector.select('wave').name == 'wave'
