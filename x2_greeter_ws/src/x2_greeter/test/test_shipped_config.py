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

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'greeter.yaml'


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
