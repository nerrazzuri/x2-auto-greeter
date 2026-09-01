"""Proves the Docker harness really built the vendor message package.

If these fail, every other @ros test is meaningless — so this runs first.
"""
import pytest

pytestmark = pytest.mark.ros


def test_the_service_types_the_greeter_needs_exist():
    from aimdk_msgs.srv import GetMcAction, PlayAudioFile, PlayTts, SetMcPresetMotion

    assert PlayTts.Request is not None
    assert PlayAudioFile.Request is not None
    assert SetMcPresetMotion.Request is not None
    assert GetMcAction.Request is not None


def test_stand_default_is_the_mode_gestures_require():
    from aimdk_msgs.msg import McAction

    assert McAction.STAND_DEFAULT == 200
    assert McAction.JOINT_DEFAULT == 100
    assert McAction.PASSIVE_DEFAULT == 1


def test_play_tts_request_has_the_fields_the_sdk_example_uses():
    from aimdk_msgs.srv import PlayTts

    req = PlayTts.Request()
    req.tts_req.text = 'hello'
    req.tts_req.domain = 'x2_greeter'
    req.tts_req.trace_id = 'trace'
    req.tts_req.is_interrupted = True
    req.tts_req.priority_weight = 0
    req.tts_req.priority_level.value = 6
    assert req.tts_req.priority_level.value == 6


def test_play_tts_response_reports_success():
    from aimdk_msgs.srv import PlayTts

    resp = PlayTts.Response()
    resp.tts_resp.is_success = True
    assert resp.tts_resp.is_success is True


def test_play_audio_file_response_field_is_misspelled_reponse():
    """The vendor .srv spells it 'reponse'. Our code must not assume otherwise."""
    from aimdk_msgs.srv import PlayAudioFile

    resp = PlayAudioFile.Response()
    assert hasattr(resp, 'reponse')
    assert not hasattr(resp, 'response')


def test_preset_motion_request_takes_a_motion_and_an_area():
    from aimdk_msgs.msg import McControlArea, McPresetMotion
    from aimdk_msgs.srv import SetMcPresetMotion

    req = SetMcPresetMotion.Request()
    motion = McPresetMotion()
    motion.value = 1002          # wave
    area = McControlArea()
    area.value = 2               # right hand
    req.motion = motion
    req.area = area
    req.interrupt = False
    assert req.motion.value == 1002


def test_rclpy_and_cv_bridge_are_importable():
    import cv_bridge
    import rclpy

    assert hasattr(rclpy, 'init')
    assert hasattr(cv_bridge, 'CvBridge')
