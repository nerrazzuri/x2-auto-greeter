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
    'hug', 'wave_goodbye', 'clap', 'cross_arms', 'scratch_head',
    'grab_buttocks', 'dynamic_light_wave',
}


def test_default_enabled_matches_the_spec():
    assert set(DEFAULT_ENABLED) == EXPECTED_ENABLED


def test_catalogue_also_carries_the_disabled_gestures():
    assert EXPECTED_DISABLED <= set(CATALOGUE)
    assert set(CATALOGUE) == EXPECTED_ENABLED | EXPECTED_DISABLED


@pytest.mark.parametrize(('name', 'motion_id'), [
    ('wave', 1002), ('salute', 1013), ('handshake', 1003), ('raise_hand', 1001),
    ('raise_both', 1010), ('bow', 3001), ('high_five', 1008), ('wave_chest', 1011),
    ('cheer', 3011), ('blow_kiss', 1004), ('heart', 1007),
    ('hug', 3008), ('wave_goodbye', 3031), ('clap', 3017), ('cross_arms', 3009),
    ('scratch_head', 3024), ('grab_buttocks', 3025), ('dynamic_light_wave', 3007),
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


def test_heart_supports_both_hands_and_either_single_hand():
    heart = CATALOGUE['heart']
    rng = random.Random(0)
    assert resolve_area(heart, 'both', rng) == AREA_BOTH
    assert resolve_area(heart, 'left', rng) == AREA_LEFT
    assert resolve_area(heart, 'right', rng) == AREA_RIGHT


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
