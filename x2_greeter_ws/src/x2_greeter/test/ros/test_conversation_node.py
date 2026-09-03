"""The node: one turn, end to end, with everything faked below it.

This is a wiring test. The behaviour it checks is ordering and delegation --
who gets called, with what, in which order. The rules themselves are tested
in cognition/conversation.py, core/scene.py etc. where they live.

Imports of rclpy and x2_greeter.ros.conversation_node (which itself imports
rclpy and aimdk_msgs) are deferred into functions rather than done at module
scope, matching every other file in test/ros/ (see test_head.py's
_head_class()) -- aimdk_msgs is only installed in the Docker harness, and a
module-level import would turn "deselected on host" into a collection error.

Corrected against the implemented code (Task 14's controller corrections),
not the original task brief: the conversation FSM lives at
x2_greeter.cognition.conversation, not x2_greeter.core.conversation.
"""
import pytest

pytestmark = pytest.mark.ros

from x2_greeter.cognition.conversation import SessionState  # noqa: E402
from x2_greeter.cognition.dialogue import Turn  # noqa: E402
from x2_greeter.cognition.transcriber import Utterance  # noqa: E402
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot  # noqa: E402

import numpy as np


def _conversation_node_class():
    from x2_greeter.ros.conversation_node import ConversationNode
    return ConversationNode


@pytest.fixture(scope='module', autouse=True)
def ros():
    import rclpy
    rclpy.init()
    yield
    rclpy.shutdown()


class _Recorder:
    """Records the order every fake adapter was called in."""

    def __init__(self):
        self.calls = []

    def note(self, what, *args):
        self.calls.append((what, args))

    def names(self):
        return [name for name, _ in self.calls]


class _FakeFace:
    def __init__(self, log):
        self.log = log
        self.shown = []

    def show_thinking(self):
        self.log.note('face.thinking')
        return True

    def show(self, name, mode=1):
        self.log.note('face.show', name)
        self.shown.append(name)
        return True

    def clear(self):
        self.log.note('face.clear')
        return True


class _FakeSpeech:
    def __init__(self, log):
        self.log = log
        self.said = []

    def say(self, text, language='en'):
        self.log.note('speech.say', text, language)
        self.said.append((text, language))
        return True


class _FakeGestures:
    def __init__(self, log, allowed=True):
        self.log = log
        self.allowed = allowed
        self.played = []

    def play(self, name=None):
        self.log.note('gesture.play', name)
        if not self.allowed:
            return False
        self.played.append(name)
        return True


class _FakeHead:
    def __init__(self, log):
        self.log = log
        self.yaws = []

    def look_at(self, yaw):
        self.yaws.append(yaw)
        return True

    def centre(self):
        self.log.note('head.centre')
        return True

    def sweep(self, on_capture=None, sleep=None):
        self.log.note('head.sweep')
        return True

    def destroy(self):
        pass


class _FakeTranscriber:
    name = 'fake'

    def __init__(self, log, utterance=None):
        self.log = log
        self.utterance = utterance or Utterance('hello there', 'en', 0.95)

    def transcribe(self, pcm, sample_rate=16000):
        self.log.note('transcribe', len(pcm))
        return self.utterance


class _FakeBackend:
    def __init__(self, log, turn=None, raises=None):
        self.log = log
        self.turn = turn or Turn('Nice to meet you.', 'en', 'wave', 'happy',
                                 False)
        self.raises = raises
        self.last = None

    def respond(self, base_frame, frame, scene, venue, history, utterance,
                language, child):
        self.log.note('backend.respond')
        self.last = dict(base_frame=base_frame, frame=frame, scene=scene,
                         history=tuple(history), utterance=utterance,
                         language=language, child=child)
        if self.raises is not None:
            raise self.raises
        return self.turn


def _person(distance=1.4, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=0.0, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL):
    people = people or (_person(),)
    return SceneSnapshot(people=people,
                         subject=people[0] if mode is AddressingMode.INDIVIDUAL
                         else None, mode=mode, at_s=0.0)


@pytest.fixture
def node():
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=None, safety_gate=None, start_worker=False)
    node.log = log
    yield node
    node.destroy_node()


def _drive_turn(node, pcm=b'\x00' * 3200):
    """Push one utterance through the worker body synchronously."""
    node._handle_utterance(pcm, at_s=1.0)


def test_a_session_starts_when_somebody_is_seen(node):
    node._on_scene(_scene(), at_s=0.0)
    assert node.conversation.state is SessionState.GREETING
    assert node.speech.said[0][0] != ''


def test_the_opening_line_comes_from_the_venue(node):
    node._on_scene(_scene(), at_s=0.0)
    assert node.speech.said[0][0] == node.venue.opening_for('en')


def _close_reason():
    from x2_greeter.cognition.conversation import CloseReason
    return CloseReason.SILENCE


def test_nothing_starts_while_the_cooldown_is_running(node):
    node._on_scene(_scene(), at_s=0.0)
    node.conversation.close(node.conversation.close_reason
                            or _close_reason(), at_s=1.0)
    said = len(node.speech.said)
    node._on_scene(_scene(), at_s=2.0)
    assert len(node.speech.said) == said


def test_the_thinking_face_comes_before_transcription(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    names = node.log.names()
    assert names.index('face.thinking') < names.index('transcribe'), (
        'shown at t=0.05 s: it is the whole of the latency mitigation, and '
        'putting it after transcription gives back the silence it removes')


def test_one_turn_walks_transcribe_respond_gesture_speak(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    # The opening line itself is a 'speech.say' (covered by
    # test_a_session_starts_when_somebody_is_seen); clear it here so this
    # assertion is only about the ordering within one *turn*, not polluted
    # by the greeting that necessarily precedes it in this fixture.
    node.log.calls.clear()
    _drive_turn(node)
    names = [n for n in node.log.names()
             if n in ('transcribe', 'backend.respond', 'gesture.play',
                      'speech.say')]
    assert names == ['transcribe', 'backend.respond', 'gesture.play',
                     'speech.say']


def test_a_raw_camera_frame_reaches_the_backend_as_a_jpeg(node):
    """Production path: FrameSource/EnvCamera hand the node raw numpy
    arrays (set here directly, bypassing detection/scene observation --
    those are exercised elsewhere and are not what this test is about).
    ClaudeDialogueBackend.respond() reads .media_type/.data off both
    frame and base_frame, so a raw ndarray must never reach it -- only a
    core.imaging.JpegFrame (or, for a test double, whatever non-ndarray
    object was handed in, untouched -- see
    test_the_backend_gets_a_fresh_frame_every_turn below).
    """
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._base_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    node._frame = np.zeros((4, 4, 3), dtype=np.uint8)
    _drive_turn(node)
    for sent in (node.backend.last['base_frame'], node.backend.last['frame']):
        assert not isinstance(sent, np.ndarray)
        assert sent.media_type == 'image/jpeg'
        assert len(sent.data) > 0


def test_the_backend_gets_a_fresh_frame_every_turn(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._on_frame(object(), None, 0.6)
    first = object()
    node._on_frame(first, None, 0.9)
    _drive_turn(node)
    assert node.backend.last['frame'] is first, (
        '每轮都看 -- the per-turn frame is the newest one, not the one from '
        'the start of the session')


def test_the_base_frame_is_captured_once_and_reused(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    base = object()
    node._on_base_frame(base, 0.1)
    _drive_turn(node)
    first_base = node.backend.last['base_frame']
    _drive_turn(node)
    assert node.backend.last['base_frame'] is first_base is base


def test_child_mode_reaches_the_backend(node):
    node._on_scene(_scene(_person(distance=1.2, child=True)), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.backend.last['child'] is True


def test_an_empty_transcription_does_not_call_the_backend(node):
    node.transcriber.utterance = Utterance('', 'en', 0.0)
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert 'backend.respond' not in node.log.names()


def test_a_backend_failure_still_produces_speech(node):
    from x2_greeter.cognition.dialogue import BackendUnavailable

    node.backend.raises = BackendUnavailable('offline')
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.speech.said[-1][0] != '', (
        'the robot speaks in every failure case; silence is the one '
        'behaviour that reads as broken')


def test_a_refused_gesture_does_not_swallow_the_words(node):
    node.gestures.allowed = False
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.speech.said[-1][0] == 'Nice to meet you.'


def test_the_head_is_centred_when_the_node_shuts_down(node):
    node._on_scene(_scene(), at_s=0.0)
    node.shutdown()
    assert 'head.centre' in node.log.names()


def test_the_face_is_cleared_when_the_session_closes(node):
    from x2_greeter.cognition.conversation import CloseReason

    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._close(CloseReason.SILENCE, at_s=20.0)
    assert 'face.clear' in node.log.names()


def test_a_disabled_head_is_never_asked_to_sweep():
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=None, safety_gate=None, start_worker=False,
        overrides={'head.sweep_on_start': False})
    node.log = log
    node._on_scene(_scene(), at_s=0.0)
    assert 'head.sweep' not in log.names()
    node.destroy_node()


# -- Fix round 2 ------------------------------------------------------------
#
# Findings 1/4: the cooldown was a permanent lockout (reset() was never
# called from _on_tick) and _after_close() was level-triggered (it fired
# once per tick for the whole ~20 s cooldown window instead of once at the
# transition). Finding 2: rclpy-entered callback bodies must not let an
# exception escape. Finding 3: head.gaze_follow ships as a config flag but
# nothing read it. Finding 5: Face's default spin_until would re-enter the
# MultiThreadedExecutor already spinning this node from the worker thread.


class _FakeAgentMode:
    def set_only_voice(self):
        return True


class _FakeModeGuard:
    def gesturing_allowed(self):
        return True


def test_a_new_session_can_start_after_the_cooldown_expires(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    assert node.conversation.state is SessionState.LISTENING
    silence_timeout_s = node.get_parameter('conversation.silence_timeout_s').value
    cooldown_s = node.get_parameter('conversation.cooldown_s').value
    close_at = 0.5 + silence_timeout_s + 1.0
    node._on_tick(at_s=close_at)
    assert node.conversation.state is SessionState.COOLDOWN

    # Ticking while still inside the cooldown window must not reset early.
    node._on_tick(at_s=close_at + 1.0)
    assert node.conversation.state is SessionState.COOLDOWN

    node._on_tick(at_s=close_at + cooldown_s + 1.0)
    assert node.conversation.state is SessionState.IDLE, (
        'reset() must run once the cooldown window has actually elapsed, '
        'or a process can only ever hold one conversation for its whole '
        'lifetime')

    said_before = len(node.speech.said)
    node._on_scene(_scene(), at_s=close_at + cooldown_s + 2.0)
    assert node.conversation.state is SessionState.GREETING, (
        'a fresh person after the cooldown must start a full second session')
    assert len(node.speech.said) > said_before


def test_after_close_fires_exactly_once_across_the_cooldown_window(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    silence_timeout_s = node.get_parameter('conversation.silence_timeout_s').value
    close_at = 0.5 + silence_timeout_s + 1.0
    node._on_tick(at_s=close_at)
    assert node.conversation.state is SessionState.COOLDOWN
    assert node.log.names().count('face.clear') == 1

    for i in range(1, 6):
        node._on_tick(at_s=close_at + i)
    assert node.conversation.state is SessionState.COOLDOWN
    assert node.log.names().count('face.clear') == 1, (
        '_after_close must fire exactly once at the transition into '
        'COOLDOWN, not once per tick for the whole cooldown window')


def test_on_scene_does_not_propagate_when_speech_raises(node):
    def _raise(*args, **kwargs):
        raise RuntimeError('boom')

    node.speech.say = _raise
    node._on_scene(_scene(), at_s=0.0)             # must not raise
    assert node.conversation.state is SessionState.GREETING


def test_on_frame_does_not_propagate_when_on_scene_raises(node):
    node._detections = lambda rgb: []               # bypass the real detector

    def _raise(*args, **kwargs):
        raise RuntimeError('boom')

    node._on_scene = _raise
    node._on_frame(np.zeros((4, 4, 3), dtype=np.uint8), None, 0.0)  # must not raise


def test_on_tick_does_not_propagate_when_after_close_raises(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)

    def _raise():
        raise RuntimeError('boom')

    node.face.clear = _raise
    silence_timeout_s = node.get_parameter('conversation.silence_timeout_s').value
    node._on_tick(at_s=0.5 + silence_timeout_s + 1.0)  # must not raise
    assert node.conversation.state is SessionState.COOLDOWN, (
        'the FSM transition must land even though _after_close raised')


def test_on_utterance_does_not_propagate_on_an_unexpected_queue_error(node):
    def _raise(*args, **kwargs):
        raise RuntimeError('boom')

    node._queue.put_nowait = _raise
    node._on_utterance(b'\x00' * 10, 0.0)            # must not raise


def test_greeting_finished_does_not_propagate_when_greeted_raises(node):
    node._on_scene(_scene(), at_s=0.0)

    def _raise(at_s):
        raise RuntimeError('boom')

    node.conversation.greeted = _raise
    node._greeting_finished(at_s=0.5)                # must not raise


def _off_centre_person():
    return Person(bbox=(500, 0, 620, 200), confidence=0.9, distance_m=1.4,
                  center_offset=0.5, stature_m=1.7, likely_child=False)


def test_gaze_follow_commands_a_clamped_yaw_for_an_off_centre_subject():
    from x2_greeter.core.gaze import MAX_YAW_RAD

    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=_FakeAgentMode(), safety_gate=_FakeModeGuard(),
        start_worker=False,
        overrides={'head.enabled': True, 'head.gaze_follow': True})
    node.log = log
    node._on_scene(_scene(), at_s=0.0)               # locks mode to INDIVIDUAL
    subject = _off_centre_person()
    snapshot = SceneSnapshot(people=(subject,), subject=subject,
                             mode=AddressingMode.INDIVIDUAL, at_s=1.0)
    node._update_gaze(snapshot, image_width=640, at_s=1.0)
    assert node.head.yaws, 'expected a look_at() command'
    yaw = node.head.yaws[-1]
    assert yaw != 0.0
    assert abs(yaw) <= MAX_YAW_RAD + 1e-9
    node.destroy_node()


def test_gaze_follow_commands_nothing_when_head_disabled():
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=_FakeAgentMode(), safety_gate=_FakeModeGuard(),
        start_worker=False,
        overrides={'head.enabled': False, 'head.gaze_follow': True})
    node.log = log
    node._on_scene(_scene(), at_s=0.0)
    subject = _off_centre_person()
    snapshot = SceneSnapshot(people=(subject,), subject=subject,
                             mode=AddressingMode.INDIVIDUAL, at_s=1.0)
    node._update_gaze(snapshot, image_width=640, at_s=1.0)
    assert node.head.yaws == []
    node.destroy_node()


def test_gaze_follow_commands_nothing_when_gaze_follow_disabled():
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=_FakeAgentMode(), safety_gate=_FakeModeGuard(),
        start_worker=False,
        overrides={'head.enabled': True, 'head.gaze_follow': False})
    node.log = log
    node._on_scene(_scene(), at_s=0.0)
    subject = _off_centre_person()
    snapshot = SceneSnapshot(people=(subject,), subject=subject,
                             mode=AddressingMode.INDIVIDUAL, at_s=1.0)
    node._update_gaze(snapshot, image_width=640, at_s=1.0)
    assert node.head.yaws == []
    node.destroy_node()


def test_gaze_follow_stops_after_the_session_closes():
    """R21: Conversation.mode is set at start() but is NOT cleared by
    close()/_commit_close() -- it only resets once the full cooldown_s has
    elapsed (Fix round 2's Finding 1). Without gating _update_gaze on
    Conversation.active as well, a still-visible person would keep being
    tracked for the whole cooldown window, un-centring the head that
    _after_close() had just centred once.
    """
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=_FakeAgentMode(), safety_gate=_FakeModeGuard(),
        start_worker=False,
        overrides={'head.enabled': True, 'head.gaze_follow': True})
    node.log = log
    subject = _off_centre_person()
    snapshot = SceneSnapshot(people=(subject,), subject=subject,
                             mode=AddressingMode.INDIVIDUAL, at_s=0.0)

    node._on_scene(_scene(subject), at_s=0.0)        # locks mode to INDIVIDUAL
    node._greeting_finished(at_s=0.5)
    node._update_gaze(snapshot, image_width=640, at_s=1.0)
    assert node.head.yaws, 'expected the head to be tracking during the session'

    silence_timeout_s = node.get_parameter('conversation.silence_timeout_s').value
    close_at = 0.5 + silence_timeout_s + 1.0
    node._on_tick(at_s=close_at)
    assert node.conversation.state is SessionState.COOLDOWN
    assert 'head.centre' in node.log.names()

    yaws_at_close = len(node.head.yaws)
    # The person is still standing there (same snapshot); further frames
    # must not resume tracking for the rest of the cooldown window.
    node._update_gaze(snapshot, image_width=640, at_s=close_at + 1.0)
    node._update_gaze(snapshot, image_width=640, at_s=close_at + 2.0)
    assert len(node.head.yaws) == yaws_at_close, (
        'gaze-follow must stop once the session closes -- _after_close() '
        'only centres the head once, and a further look_at() on the next '
        'frame would silently un-centre it for the whole cooldown window')
    node.destroy_node()


def test_face_gets_a_non_default_spin_until():
    from x2_greeter.ros.conversation_node import _face_spin_until
    from x2_greeter.ros.face import _default_spin

    log = _Recorder()
    node = _conversation_node_class()(
        speech=_FakeSpeech(log), gestures=_FakeGestures(log),
        head=_FakeHead(log), transcriber=_FakeTranscriber(log),
        backend=_FakeBackend(log), audio_source=None, frame_source=None,
        env_camera=None, agent_mode=_FakeAgentMode(),
        safety_gate=_FakeModeGuard(), start_worker=False)
    node.log = log
    assert node.face._spin is _face_spin_until, (
        'Face is spinning via rclpy.spin_until_future_complete from the '
        'worker thread -- exactly the re-entrant-executor call service_call.'
        'wait_for_future exists to avoid')
    assert node.face._spin is not _default_spin
    node.destroy_node()


def test_after_close_centres_the_head():
    log = _Recorder()
    node = _conversation_node_class()(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=_FakeAgentMode(), safety_gate=_FakeModeGuard(),
        start_worker=False)
    node.log = log
    from x2_greeter.cognition.conversation import CloseReason

    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._close(CloseReason.SILENCE, at_s=20.0)
    assert 'head.centre' in node.log.names(), (
        'gaze-follow can leave the head off-centre; every exit path must '
        'still centre it')
    node.destroy_node()
