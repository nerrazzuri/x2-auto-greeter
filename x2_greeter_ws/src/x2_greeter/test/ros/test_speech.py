import random
import threading

import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def rig(ros):
    """FakeRobot plus a caller node, both spinning."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=False)
    caller = ros.create_node('speech_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot, caller
    executor.shutdown()
    caller.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def dispatcher(caller, **kwargs):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.speech import SpeechDispatcher

    kwargs.setdefault('rng', random.Random(0))
    kwargs.setdefault('callback_group', ReentrantCallbackGroup())
    return SpeechDispatcher(caller, **kwargs)


def test_it_speaks_through_play_tts(rig):
    robot, caller = rig
    speech = dispatcher(caller)
    assert speech.speak('Hello there!') is True
    assert robot.tts_requests[0].tts_req.text == 'Hello there!'


def test_it_uses_the_configured_domain_and_priority(rig):
    robot, caller = rig
    dispatcher(caller, domain='x2_greeter', priority_level=6).speak('Hi')
    req = robot.tts_requests[0].tts_req
    assert req.domain == 'x2_greeter'
    assert req.priority_level.value == 6
    assert req.is_interrupted is True


def test_it_stamps_the_request_header(rig):
    robot, caller = rig
    dispatcher(caller).speak('Hi')
    stamp = robot.tts_requests[0].header.header.stamp
    assert stamp.sec > 0 or stamp.nanosec > 0


def test_it_refuses_to_speak_nothing(rig):
    robot, caller = rig
    assert dispatcher(caller).speak('   ') is False
    assert robot.tts_requests == []


def test_auto_demotes_to_audio_files_when_tts_reports_failure(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='auto', audio_dir='/var/tmp/x2_greeter_audio',
                        audio_file_count=6)
    assert speech.speak('Hello there!') is True     # succeeded via the audio file
    assert speech.demoted is True
    assert speech.using_tts is False
    assert len(robot.audio_requests) == 1


def test_demotion_is_one_way_within_a_session(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='auto')
    speech.speak('first')
    robot.tts_succeeds = True                        # TTS recovers
    speech.speak('second')
    assert len(robot.tts_requests) == 1              # never tried again
    assert len(robot.audio_requests) == 2


def test_the_audio_request_matches_the_documented_format(rig):
    robot, caller = rig
    speech = dispatcher(caller, tier='audio_file', audio_dir='/var/tmp/x2_greeter_audio',
                        audio_file_count=6)
    assert speech.speak('ignored, a recording is played instead') is True
    audio = robot.audio_requests[0].file
    assert audio.pkg_name == 'x2_greeter'
    # Trailing separator always present, matching the vendor's own client.
    assert audio.file_path == '/var/tmp/x2_greeter_audio/'
    assert audio.file_name.startswith('greeting_')
    assert audio.file_name.endswith('.wav')
    assert audio.info.channels == 1
    assert audio.info.sample_rate == 16000
    assert audio.info.sample_format == 'S16_LE'
    assert audio.info.coding_format == 'wave'
    assert audio.priority == 6


def test_audio_file_tier_never_calls_tts(rig):
    robot, caller = rig
    dispatcher(caller, tier='audio_file').speak('Hi')
    assert robot.tts_requests == []


def test_pinned_tts_tier_never_falls_back_to_audio_files(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='tts')
    assert speech.speak('Hello') is False
    assert robot.audio_requests == []
    assert speech.demoted is False


def test_the_audio_file_index_stays_within_the_deployed_range(rig):
    robot, caller = rig
    speech = dispatcher(caller, tier='audio_file', audio_file_count=3,
                        rng=random.Random(11))
    for _ in range(20):
        speech.speak('x')
    indices = {int(r.file.file_name[len('greeting_'):-len('.wav')])
               for r in robot.audio_requests}
    assert indices <= {0, 1, 2}


def test_it_rejects_an_unknown_tier(rig):
    robot, caller = rig
    with pytest.raises(ValueError, match='speech.tier'):
        dispatcher(caller, tier='telepathy')


def test_it_reports_failure_when_the_service_is_absent(ros):
    """No robot at all: speak() must return False, not hang or raise."""
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='tts')
        assert speech.speak('anyone there?') is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)


def test_it_reports_failure_when_the_audio_file_is_rejected(ros):
    """A responder that rejects every PlayAudioFile call: speak() must return False.

    Audio files are the last fallback tier, so this pins the rejection branch
    with a test-local service node rather than FakeRobot (which always
    succeeds and must stay untouched for tasks 14/15).

    Rejection is expressed the way the vendor's own client reads success --
    through `status`, not `header.code` (play_audio.py checks
    `resp.reponse.status.value == 1`). Field is misspelled 'reponse' (sic) in
    the real .srv, not 'response'.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import PlayAudioFile
    from x2_greeter.ros.speech import AUDIO_SERVICE

    class RejectingAudioServer(Node):
        def __init__(self):
            super().__init__('rejecting_audio_server')
            self.create_service(PlayAudioFile, AUDIO_SERVICE, self._on_play_audio_file)

        def _on_play_audio_file(self, request, response):
            response.reponse.header.code = 1
            response.reponse.status.value = CommonState.INVALID
            return response

    server = RejectingAudioServer()
    caller = ros.create_node('rejecting_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='audio_file')
        assert speech.speak('Hi') is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_it_reports_failure_when_the_header_is_clean_but_status_reports_failure(ros):
    """The exact case the header-only success check gets wrong.

    header.code is default-zero, so a server that reports failure only
    through `status` and never touches the header must still be read as a
    failure -- not as success because the header happens to be clean.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.msg import CommonState
    from aimdk_msgs.srv import PlayAudioFile
    from x2_greeter.ros.speech import AUDIO_SERVICE

    class HeaderCleanFailureServer(Node):
        def __init__(self):
            super().__init__('header_clean_failure_audio_server')
            self.create_service(PlayAudioFile, AUDIO_SERVICE, self._on_play_audio_file)

        def _on_play_audio_file(self, request, response):
            # header.code left at its default 0 ("success" if you only look
            # at the header); status reports the real, failing answer.
            response.reponse.status.value = CommonState.FAILURE
            return response

    server = HeaderCleanFailureServer()
    caller = ros.create_node('header_clean_failure_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='audio_file')
        assert speech.speak('Hi') is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_it_reports_success_when_only_the_header_reports_it(ros):
    """A server that fills only the header is still understood as success.

    status.value is left at its default (UNKNOWN, 0) and header.code is left
    at its default (0) -- the mirror image of
    test_it_reports_failure_when_the_header_is_clean_but_status_reports_failure.
    A server that speaks only through the header, and cleanly, is the best
    signal available from it, and must not be misread as a rejection.
    """
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node

    from aimdk_msgs.srv import PlayAudioFile
    from x2_greeter.ros.speech import AUDIO_SERVICE

    class HeaderOnlySuccessServer(Node):
        def __init__(self):
            super().__init__('header_only_success_audio_server')
            self.create_service(PlayAudioFile, AUDIO_SERVICE, self._on_play_audio_file)

        def _on_play_audio_file(self, request, response):
            # response.reponse.status.value left at its default (UNKNOWN);
            # response.reponse.header.code left at its default (0).
            return response

    server = HeaderOnlySuccessServer()
    caller = ros.create_node('header_only_success_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='audio_file')
        assert speech.speak('Hi') is True
    finally:
        executor.shutdown()
        caller.destroy_node()
        server.destroy_node()
        thread.join(timeout=5.0)


def test_it_reports_failure_when_the_audio_service_is_absent(ros):
    """No robot at all, audio_file tier: speak() must return False, not hang or raise."""
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_audio_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='audio_file')
        assert speech.speak('anyone there?') is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)
