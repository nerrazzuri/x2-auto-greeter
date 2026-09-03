"""Every catalogued (motion id, area) pair must come from a named vendor source.

The narrower version of this rule -- "the motion id must be a member of
McPresetMotion" -- is not sufficient and never was. The vendor ships two
sources that disagree and neither is a superset of the other: the enum omits
1007, 1010, 1011, 3017, 3024, 3025 and 3031; the interface doc's table omits
4001, 4002 and the whole of area 4. A catalogue entry pinned against only one
of them is pinned against half the evidence.

So each spec names its source and is checked against that source. The area is
part of the check because the area is what went wrong last time: 1007 is a
two-handed motion, Phase 1 sent it area 2, the controller refused it, and the
refusal was written down as "1007 does not exist".

Lives in the ros suite because the enum half needs aimdk_msgs.
"""
import pytest

from x2_greeter.core.gestures import AREA_BOTH, CATALOGUE, DEFAULT_ENABLED, VALID_SOURCES

pytestmark = pytest.mark.ros

# Transcribed from the interface docs, preset_motion.html (Step 1 of the task):
# sdk/aimdk-aarch64-a424add7-artifacts/docs/cn/_build/html/en/dev/Interface/
# control_mod/preset_motion.html -- the "Action Name / motion / area" table.
# Grouped by motion id; the trailing comment is the vendor's own action name(s)
# for that id, left-hand/right-hand/both-hand rows collapsed onto one line.
DOC_TABLE_COMBINATIONS = frozenset({
    (1001, 1), (1001, 2),              # Left/Right-hand raise
    (1002, 1), (1002, 2),              # Left/Right-hand wave
    (1003, 1), (1003, 2),              # Left/Right-hand handshake
    (1004, 1), (1004, 2),              # Left/Right-hand blow a kiss
    (1007, 3),   # 双手比心 / two-handed heart at the chest
    (1007, 1), (1007, 2),              # Left/Right-hand heart gesture
    (1008, 1), (1008, 2),              # Left/Right-hand high-five
    (1010, 1), (1010, 2), (1010, 3),   # Raise left/right/both hand(s)
    (1011, 1), (1011, 2),              # Left/Right-hand wave at chest
    (1013, 1), (1013, 2),              # Left/Right-hand salute
    (3001, 11),                        # Bow
    (3007, 11),                        # Dynamic light wave
    (3008, 11),                        # Hug
    (3009, 11),                        # Cross arms
    (3011, 11),                        # Cheer
    (3017, 11),                        # Clap
    (3024, 11),                        # Scratch head
    (3025, 11),                        # Grab buttocks
    (3031, 11),                        # Wave goodbye
})


def _enum_motion_ids():
    from aimdk_msgs.msg import McPresetMotion
    return {value for name, value in vars(McPresetMotion).items()
            if isinstance(value, int) and name.isupper()}


def test_every_spec_declares_a_source_we_recognise():
    bad = {name: spec.source for name, spec in CATALOGUE.items()
           if spec.source not in VALID_SOURCES}
    assert not bad, (
        f'gesture(s) with no usable provenance: {bad}. Every entry must say '
        f'where its id came from -- one of {sorted(VALID_SOURCES)} -- because '
        f'"I read it somewhere" is how three invented ids reached hardware.')


def test_every_enum_sourced_motion_id_is_in_the_vendor_enum():
    known = _enum_motion_ids()
    invented = {name: spec.motion_id for name, spec in CATALOGUE.items()
                if spec.source == 'enum' and spec.motion_id not in known}
    assert not invented, (
        f'gesture(s) claiming the enum as their source but absent from it: '
        f'{invented}')


def test_every_doc_sourced_pair_is_in_the_doc_table():
    missing = {
        name: (spec.motion_id, area)
        for name, spec in CATALOGUE.items() if spec.source == 'doc_table'
        for area in spec.areas
        if (spec.motion_id, area) not in DOC_TABLE_COMBINATIONS
    }
    assert not missing, (
        f'gesture(s) claiming the doc table as their source with a pair the '
        f'table does not list: {missing}')


def test_the_default_enabled_gestures_are_all_real():
    # Called out separately so a failure names the ones a live robot would
    # actually have tried to perform.
    enum_ids = _enum_motion_ids()
    broken = []
    for name in DEFAULT_ENABLED:
        spec = CATALOGUE[name]
        for area in spec.areas:
            ok = (spec.motion_id in enum_ids
                  if spec.source == 'enum'
                  else (spec.motion_id, area) in DOC_TABLE_COMBINATIONS)
            if not ok:
                broken.append((name, spec.motion_id, area, spec.source))
    assert not broken, f'shipped-enabled gestures with unsourced pairs: {broken}'


def test_the_chest_heart_is_1007_performed_with_both_hands():
    # The correction this file exists to hold. 1007 is the two-handed heart at
    # the chest and it takes area 3. Sending it area 2 -- which is what
    # handed=True plus the shipped right-hand preference did -- is a
    # one-handed area for a two-handed motion, and the controller refuses it
    # silently. That refusal was once mistaken for the id being invented.
    spec = CATALOGUE['heart']
    assert spec.motion_id == 1007
    assert spec.areas == (AREA_BOTH,)
    assert spec.handed is False, (
        'heart must not follow hand_preference: there is no one-handed '
        'variant of it, so honouring a preference can only produce an area '
        'the motion does not accept')
    assert spec.source == 'doc_table'


def test_the_overhead_heart_is_a_separate_disabled_gesture():
    from aimdk_msgs.msg import McPresetMotion

    spec = CATALOGUE['heart_overhead']
    assert spec.motion_id == McPresetMotion.INTERACTION_SWEATHEART == 3004
    assert spec.source == 'enum'
    assert spec.hw_verified is False
    assert 'heart_overhead' not in DEFAULT_ENABLED, (
        'nobody has watched this one run: the area is an inference from the '
        'enum, not a row in the doc table, so it stays off until the bench '
        'check')


def test_only_gestures_actually_seen_on_hardware_are_marked_verified():
    verified = {name for name, spec in CATALOGUE.items() if spec.hw_verified}
    assert verified == {'wave'}, (
        f'hw_verified means a human watched the robot perform it, not that we '
        f'believe the id is right. Currently that is wave (1002, area 2) and '
        f'nothing else; got {sorted(verified)}. Widen this set only after the '
        f'bench check, in the same commit that records what was observed.')
