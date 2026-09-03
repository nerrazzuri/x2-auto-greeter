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
