"""Assert the configuration the robot actually ships with, not a description of it.

No other test in the repo reads config/greeter.yaml: writing bad values into
the shipped file (require_stand_default: false, a near-zero confidence_min, a
wide-open distance_max_m) leaves the rest of the suite fully green, because
every other test constructs its own GateConfig/PresenceConfig/ModeGuard
directly. This file is the only thing standing between a one-line edit to the
shipped YAML and a disabled safety interlock reaching real hardware unnoticed.

Needs no ROS -- it is plain YAML -- so it is left unmarked, like
test_audio_tooling.py, and runs on both host and container.
"""
from pathlib import Path

import pytest
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'greeter.yaml'
CONVERSATION_PATH = Path(__file__).resolve().parents[1] / 'config' / 'conversation.yaml'
VENUES_DIR = Path(__file__).resolve().parents[1] / 'config' / 'venues'


def _params():
    with open(CONFIG_PATH, 'r') as handle:
        doc = yaml.safe_load(handle)
    return doc['/**']['ros__parameters']


def test_the_shipped_config_exists():
    assert CONFIG_PATH.is_file(), CONFIG_PATH


def test_the_detection_gates_ship_at_their_spec_values():
    detect = _params()['detect']
    assert detect['confidence_min'] == 0.5, (
        'confidence_min must ship at the spec value: lowering it lets weaker, '
        'less certain detections trigger a greeting')
    assert detect['distance_min_m'] == 1.0, (
        'distance_min_m must ship at the spec value: lowering it is exactly '
        'the change that would let a person closer than the gesture safety '
        'floor be greeted without anyone noticing the gate moved')
    assert detect['distance_max_m'] == 3.0, (
        'distance_max_m must ship at the spec value: widening it greets '
        'people who are not actually approaching the robot')
    assert detect['center_tolerance'] == 0.25, (
        'center_tolerance must ship at the spec value: widening it greets '
        'people who are only passing through the edge of frame')


def test_the_stand_default_interlock_is_enabled_by_default():
    safety = _params()['safety']
    assert safety['require_stand_default'] is True, (
        'safety.require_stand_default must ship true: this is the interlock '
        'that keeps the robot from swinging an arm outside STAND_DEFAULT, '
        'and it is a load-bearing safety property, not a tuning knob')


def test_the_backend_timeout_cannot_outlive_the_confirmation_window():
    params = _params()
    backend_timeout_s = params['backend']['timeout_s']
    confirm_timeout_s = params['presence']['confirm_timeout_s']
    assert backend_timeout_s < confirm_timeout_s, (
        f'backend.timeout_s ({backend_timeout_s}) must stay below '
        f'presence.confirm_timeout_s ({confirm_timeout_s}): if the cloud '
        'call is allowed to run longer than the presence tracker waits for '
        'it, a greeting worker can outlive the confirmation window and '
        'apply its verdict to somebody who has since walked off')


def test_the_input_source_priority_stays_below_the_remote_controller():
    mc_input = _params()['mc_input']
    assert 20 <= mc_input['priority'] <= 39, (
        f"mc_input.priority ({mc_input['priority']}) must ship inside the "
        'documented SDK band 20-39. The remote controller arbitrates at 80 '
        'and 100-80 is reserved for emergency stop and safety modes, so a '
        'greeter registered at or above 80 would take away the operator\'s '
        'ability to override it by picking the remote up')


def _conversation_params():
    with CONVERSATION_PATH.open(encoding='utf-8') as handle:
        return yaml.safe_load(handle)['/**']['ros__parameters']


# --- the hardware gates ---------------------------------------------------

@pytest.mark.parametrize('path', [
    ('face', 'enabled'),
    ('head', 'enabled'),
    ('head', 'sweep_on_start'),
    ('head', 'gaze_follow'),
    ('base_frame', 'use_env_camera'),
])
def test_every_hardware_gated_feature_ships_off(path):
    # None of these has run on this robot under our code. Turning one on is
    # a deliberate act performed on site, with a human at the stop control.
    section, key = path
    assert _conversation_params()[section][key] is False


def test_hearing_ships_on_because_a_robot_that_cannot_hear_has_no_phase_two():
    assert _conversation_params()['hearing']['enabled'] is True


# --- the safety interlocks ------------------------------------------------

def test_the_stand_default_interlock_is_on():
    assert _conversation_params()['safety']['require_stand_default'] is True


def test_the_arms_reach_interlock_is_one_metre():
    assert _conversation_params()['safety']['gesture_min_distance_m'] == 1.0


def test_the_mc_input_priority_stays_in_the_documented_band():
    priority = _conversation_params()['mc_input']['priority']
    assert 20 <= priority <= 39, (
        'the remote controller sits at 80 and must keep its override')


# --- the numbers that have to agree with each other -----------------------

def test_the_backend_gives_up_before_the_silence_timer_fires():
    params = _conversation_params()
    assert (params['dialogue']['timeout_s']
            < params['conversation']['silence_timeout_s']), (
        'a backend still thinking when the silence timer fires would close '
        'the session it is answering')


def test_the_session_cap_is_not_quietly_widened():
    assert _conversation_params()['conversation']['session_max_s'] <= 300.0


def test_the_turn_cap_is_not_quietly_widened():
    assert _conversation_params()['conversation']['max_turns'] <= 12


def test_individual_addressing_stops_where_the_spec_says():
    assert _conversation_params()['presence']['individual_max_m'] == 2.0


def test_the_scene_horizon_is_wider_than_the_addressing_distance():
    presence = _conversation_params()['presence']
    assert presence['scene_max_m'] > presence['individual_max_m']


# --- the names must exist -------------------------------------------------

def test_every_enabled_gesture_is_in_the_catalogue():
    from x2_greeter.core.gestures import CATALOGUE

    unknown = [name for name in _conversation_params()['gestures']['enabled']
               if name not in CATALOGUE]
    assert unknown == [], f'no such gesture: {unknown}'


def test_the_enabled_gestures_are_exactly_the_phase_1_default_set():
    # The brief this file was drafted from listed six gestures that do not
    # exist in the catalogue (hello, nod, clap, thumbs_up, shake_hand,
    # point_forward). Pin to core.gestures.DEFAULT_ENABLED, verbatim, so a
    # future edit back toward that fictional list fails here instead of on
    # hardware.
    from x2_greeter.core.gestures import DEFAULT_ENABLED

    assert (tuple(_conversation_params()['gestures']['enabled'])
            == DEFAULT_ENABLED)


def test_every_enabled_expression_is_in_the_catalogue():
    from x2_greeter.core.faces import CATALOGUE

    unknown = [name for name in
               _conversation_params()['face']['enabled_expressions']
               if name not in CATALOGUE]
    assert unknown == [], f'no such expression: {unknown}'


def test_the_thinking_expression_is_enabled():
    # It is the entire latency mitigation: shown at t=0.05 s while the
    # reply is still two seconds away.
    assert 'thinking' in _conversation_params()['face']['enabled_expressions']


@pytest.mark.parametrize('forbidden', ['angry', 'extreme_angry'])
def test_no_angry_expression_is_ever_enabled(forbidden):
    assert forbidden not in _conversation_params()['face']['enabled_expressions']


def test_the_configured_venue_profile_exists():
    profile = _conversation_params()['venue']['profile']
    assert (VENUES_DIR / f'{profile}.yaml').is_file()


def test_every_venue_profile_on_disk_parses():
    from x2_greeter.core.venue import load_venue

    profiles = sorted(VENUES_DIR.glob('*.yaml'))
    assert profiles, 'the venue directory is not empty'
    for path in profiles:
        load_venue(path)


# --- language -------------------------------------------------------------

def test_the_allowed_languages_are_exactly_this_phases_two():
    from x2_greeter.core.language import ALLOWED_LANGUAGES

    assert (tuple(_conversation_params()['language']['allowed'])
            == ALLOWED_LANGUAGES), (
        'Malay is next phase; adding it here without a TTS voice produces a '
        'robot that answers in a language it cannot pronounce')


def test_the_default_language_is_english():
    assert _conversation_params()['language']['default'] == 'en'


# --- the model --------------------------------------------------------------

def test_no_api_key_is_anywhere_in_the_shipped_config():
    text = CONVERSATION_PATH.read_text(encoding='utf-8')
    assert 'sk-ant' not in text
    assert 'ANTHROPIC_API_KEY' not in text, (
        'the key comes from the environment and is never written down')


# --- the vendor topics and services, corrected against real code ----------
#
# The plan this file was drafted from named five vendor endpoints that do
# not exist anywhere in the SDK or in this package's own ros/ modules. Each
# assertion below pins the corrected value against the same module that
# proved the correction, so an edit back toward the fictional name fails a
# test instead of a hardware bring-up.

def test_hearing_topic_matches_the_real_vendor_audio_source():
    # Pinned against x2_greeter.ros.audio_source.DEFAULT_TOPIC by value, not
    # by import: that module pulls in aimdk_msgs, which is not on the host.
    assert (_conversation_params()['hearing']['topic']
            == '/agent/process_audio_output')


def test_speech_service_matches_the_hardware_proven_tts_service():
    # Pinned against x2_greeter.ros.speech.TTS_SERVICE by value, not by
    # import: that module pulls in aimdk_msgs, which is not on the host.
    assert (_conversation_params()['speech']['service']
            == '/aimdk_5Fmsgs/srv/PlayTts')


def test_face_service_matches_ros_face_service():
    # Pinned against x2_greeter.ros.face.SERVICE by value, not by import:
    # that module pulls in rclpy, which is not on the host.
    assert (_conversation_params()['face']['service']
            == '/aimdk_5Fmsgs/srv/PlayEmoji')


def test_base_frame_rgb_topic_is_the_confirmed_wide_stereo_candidate():
    assert (_conversation_params()['base_frame']['rgb_topic']
            == '/aima/hal/sensor/stereo_head_front_left/rgb_image'), (
        'this is the candidate wide front-stereo topic from the SDK sensor '
        'docs; confirm it on site with `ros2 topic list` before flipping '
        'base_frame.use_env_camera on')


# --- the launch file must not quietly overrule the file above -------------
#
# ROS 2 applies a Node's `parameters` list left to right, so a dict listed
# after the params file beats the file. conversation.launch.py used to pass
# {'venue.profile': venue} with the launch argument defaulting to
# 'clothing_store', so every launch layered clothing_store over whatever
# venue.profile said -- making the key above, and HARDWARE_BRINGUP.md
# section 8 which sends the operator to it, silently wrong.
#
# The behavioural test lives in test/ros/test_conversation_launch.py, which
# imports `launch` and therefore only runs in the container. This one reads
# the file as source so the invariant is also guarded on the host gate,
# where the container suite is not run.

LAUNCH_PATH = (Path(__file__).resolve().parents[1] / 'launch'
               / 'conversation.launch.py')


def _declared_launch_arguments():
    import ast

    tree = ast.parse(LAUNCH_PATH.read_text(encoding='utf-8'))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, 'id', None) or getattr(func, 'attr', None)
        if name != 'DeclareLaunchArgument' or not node.args:
            continue
        argument = node.args[0]
        if not isinstance(argument, ast.Constant):
            continue
        default = None
        for keyword in node.keywords:
            if keyword.arg == 'default_value' and isinstance(keyword.value,
                                                             ast.Constant):
                default = keyword.value.value
        found[argument.value] = default
    return found


def test_the_venue_launch_argument_defaults_to_empty():
    arguments = _declared_launch_arguments()
    assert 'venue' in arguments, LAUNCH_PATH
    assert arguments['venue'] == '', (
        "venue:= must default to empty so that config/conversation.yaml's "
        'venue.profile is what the robot actually uses. Any non-empty '
        'default is layered over the params file on every launch, and an '
        'operator editing the YAML at the venue gets no effect and no '
        'explanation')
