"""The conversation node: wires perception, hearing, the dialogue backend,
speech, gesture and face together behind one Conversation state machine.

This node never changes the robot's motion mode and never issues a
locomotion command. Gestures are refused (but the reply is still spoken)
unless the robot is in STAND_DEFAULT, the subject is at least 1.0 m away,
and that distance reading is fresh -- the same interlock ros/greeting_node.py
uses, since no shared "SafetyGate" class exists in this codebase.

Threading: rclpy callbacks (frames, scene, audio) must never block on the
transcriber or the cloud backend. A single background worker thread owns
that work; frame/scene callbacks only ever update a fresh-frame cache and
kick off (or observe) the Conversation state machine. Exactly one
threading.Lock guards every mutation of the Conversation object, because
Conversation is not thread-safe by itself. The audio path hands utterances
to a single-slot, discard-oldest queue -- a person who keeps talking while
the robot is still answering the previous turn gets the newest thing they
said, not a backlog.

Corrected against the implemented adapters (Task 14's controller corrections),
not the original task brief:
- The FSM lives at x2_greeter.cognition.conversation, not core.conversation.
- The real adapters are SpeechDispatcher.speak(text), not Speech.say(text,
  language); GestureDispatcher.perform(motion_id, area_id), not
  Gestures.play(name). Both are wrapped below (_SpeechAdapter, _GestureGate)
  so the rest of this module can talk to them the same way the unit tests'
  fakes do.
- ros/interaction_guard.py is never imported here (Phase 1 module with a
  known defect) -- not even transitively, which is why _LoggerShim is
  redefined here rather than imported from ros/greeting_node.py.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Optional

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from x2_greeter.cognition.claude import ClaudeDialogueBackend
from x2_greeter.cognition.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.cognition.dialogue import BackendUnavailable, TurnLimits
from x2_greeter.cognition.transcriber import (
    SAMPLE_RATE_HZ, FasterWhisperTranscriber, TranscriptionUnavailable)
from x2_greeter.core.detectors import build_detector
from x2_greeter.core.faces import MODE_ONCE
from x2_greeter.core.gestures import DEFAULT_ENABLED as DEFAULT_GESTURES
from x2_greeter.core.gestures import GestureSelector
from x2_greeter.core.language import ALLOWED_LANGUAGES, LanguagePolicy
from x2_greeter.core.scene import SceneConfig, observe
from x2_greeter.core.venue import load_venue
from x2_greeter.ros.agent_mode import SERVICE as AGENT_MODE_SERVICE
from x2_greeter.ros.agent_mode import AgentMode
from x2_greeter.ros.audio_source import DEFAULT_TOPIC as HEARING_TOPIC
from x2_greeter.ros.audio_source import VendorAudioSource
from x2_greeter.ros.env_camera import EnvCamera
from x2_greeter.ros.face import SERVICE as FACE_SERVICE
from x2_greeter.ros.face import Face
from x2_greeter.ros.frame_source import FrameSource
from x2_greeter.ros.gesture import GestureDispatcher
from x2_greeter.ros.head import COMMAND_TOPIC as HEAD_COMMAND_TOPIC
from x2_greeter.ros.head import STATE_TOPIC as HEAD_STATE_TOPIC
from x2_greeter.ros.head import Head
from x2_greeter.ros.input_source import maybe_register
from x2_greeter.ros.mode_guard import ModeGuard
from x2_greeter.ros.speech import TTS_SERVICE, SpeechDispatcher

# conversation.yaml pins this list to core.gestures.DEFAULT_ENABLED verbatim
# (test_shipped_config.py), so it doubles as the node's own default.
_DEFAULT_GESTURES_ENABLED = list(DEFAULT_GESTURES)

# conversation.yaml's own list -- deliberately not core.faces.DEFAULT_ENABLED,
# which includes 'adore' instead of 'sympathy'.
_DEFAULT_FACE_ENABLED = ['thinking', 'calm', 'blink', 'happy', 'very_happy',
                         'cute', 'confused', 'sympathy']

_FALLBACK_LINES = {
    'en': "Sorry, I didn't catch that.",
    'zh': '抱歉，我没听清楚。',
}


class ConversationNode(Node):
    def __init__(self, face=None, speech=None, gestures=None, head=None,
                 transcriber=None, backend=None, audio_source=None,
                 frame_source=None, env_camera=None, agent_mode=None,
                 safety_gate=None, start_worker: bool = True,
                 overrides: Optional[dict] = None, **node_kwargs) -> None:
        super().__init__('x2_conversation', **node_kwargs)
        self._overrides = dict(overrides or {})
        self._declare_parameters(self._overrides)

        self._sensor_group = MutuallyExclusiveCallbackGroup()
        self._service_group = ReentrantCallbackGroup()

        self.venue = load_venue(self._venue_path())
        self.conversation = Conversation(
            self.venue, limits=self._conversation_limits(),
            language_policy=LanguagePolicy(
                default=self._p('language.default'),
                switch_confidence=self._p('language.switch_confidence')))
        self._scene_config = SceneConfig(
            confidence_min=float(self._p('presence.confidence_min')),
            individual_max_m=float(self._p('presence.individual_max_m')),
            scene_max_m=float(self._p('presence.scene_max_m')),
            child_stature_max_m=float(self._p('presence.child_stature_max_m')))
        self.detector = build_detector(
            self._p('detect.detector'), self._p('detect.model_dir'),
            logger=_LoggerShim(self.get_logger()))

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._queue: 'queue.Queue' = queue.Queue(maxsize=1)
        self._worker: Optional[threading.Thread] = None

        self._scene = None
        self._frame = None
        self._base_frame = None
        self._child = False
        self._latest_reading: Optional[tuple] = None

        self.mode_guard = safety_gate if safety_gate is not None else ModeGuard(
            self, require_stand_default=bool(self._p('safety.require_stand_default')),
            callback_group=self._service_group)

        self.face = face if face is not None else self._build_face()
        self.speech = speech if speech is not None else self._build_speech()
        self.gestures = gestures if gestures is not None else self._build_gestures()
        self.head = head if head is not None else self._build_head()
        self.transcriber = (transcriber if transcriber is not None
                            else self._build_transcriber())
        self.backend = backend if backend is not None else self._build_backend()

        self.audio = audio_source if audio_source is not None else self._build_audio()
        if self.audio is not None:
            self.audio.start()

        self.env_camera = (env_camera if env_camera is not None
                           else self._build_env_camera())

        self.frames = frame_source if frame_source is not None else FrameSource(
            self, rgb_topic=self._p('camera.rgb_topic'),
            depth_topic=self._p('camera.depth_topic'), on_frame=self._on_frame,
            max_sync_skew_s=float(self._p('camera.max_sync_skew_s')),
            stale_warn_s=float(self._p('camera.stale_warn_s')),
            rotate_180=bool(self._p('camera.rotate_180')),
            callback_group=self._sensor_group)

        self.agent_mode = agent_mode if agent_mode is not None else AgentMode(
            self, service=AGENT_MODE_SERVICE, callback_group=self._service_group)
        self.agent_mode.set_only_voice()

        self.input_source = maybe_register(
            self, enabled=bool(self._p('mc_input.enabled')),
            name=self._p('mc_input.name'), priority=int(self._p('mc_input.priority')),
            timeout_ms=int(self._p('mc_input.timeout_ms')),
            callback_group=self._service_group)

        self._timer = self.create_timer(1.0, self._on_tick,
                                        callback_group=self._sensor_group)

        if start_worker:
            self._worker = threading.Thread(target=self._run_worker, daemon=True)
            self._worker.start()

    # -- parameters ---------------------------------------------------------

    def _declare_parameters(self, overrides: dict) -> None:
        defaults = {
            'venue.profile': 'clothing_store',
            'presence.confidence_min': 0.5,
            'presence.individual_max_m': 2.0,
            'presence.scene_max_m': 5.0,
            'presence.child_stature_max_m': 1.35,
            'conversation.max_turns': 8,
            'conversation.silence_timeout_s': 8.0,
            'conversation.session_max_s': 180.0,
            'conversation.cooldown_s': 20.0,
            'conversation.backend_failures_max': 2,
            'conversation.history_max': 12,
            'conversation.reply_max_chars': 200,
            'language.default': 'en',
            'language.switch_confidence': 0.7,
            'language.allowed': list(ALLOWED_LANGUAGES),
            'hearing.enabled': True,
            'hearing.topic': HEARING_TOPIC,
            'hearing.max_utterance_s': 15.0,
            'hearing.model_size': 'small',
            'hearing.device': 'auto',
            'hearing.compute_type': 'int8',
            'dialogue.model': 'claude-opus-5',
            'dialogue.effort': 'low',
            'dialogue.timeout_s': 6.0,
            'speech.service': TTS_SERVICE,
            'speech.timeout_s': 10.0,
            # Conversation replies are dynamic LLM text, not the fixed
            # phrase list ros/speech.py's audio-file fallback expects (it
            # plays one of greeting_NN.wav) -- so 'auto' would silently
            # start playing a recorded greeting instead of the answer to
            # whatever was just asked. Deviation from the brief, documented
            # in the task-14 report.
            'speech.tier': 'tts',
            'speech.domain': 'x2_greeter_conversation',
            'speech.priority_level': 6,
            'speech.audio_dir': '/var/tmp/x2_greeter_audio',
            'speech.audio_file_count': 6,
            'gestures.enabled': list(_DEFAULT_GESTURES_ENABLED),
            'gestures.hand_preference': 'right',
            'face.enabled': False,
            'face.service': FACE_SERVICE,
            'face.enabled_expressions': list(_DEFAULT_FACE_ENABLED),
            'head.enabled': False,
            'head.sweep_on_start': False,
            'head.gaze_follow': False,
            'head.command_topic': HEAD_COMMAND_TOPIC,
            'head.state_topic': HEAD_STATE_TOPIC,
            'head.rate_hz': 20.0,
            'base_frame.use_env_camera': False,
            'base_frame.rgb_topic': '/aima/hal/sensor/stereo_head_front_left/rgb_image',
            'base_frame.rotate_180': False,
            'safety.require_stand_default': True,
            'safety.gesture_min_distance_m': 1.0,
            'safety.stale_distance_s': 1.0,
            'mc_input.name': 'x2_greeter',
            'mc_input.priority': 30,
            # Not present in conversation.yaml (which only ships name and
            # priority); defaults matched to greeter.yaml so both nodes
            # behave the same way absent an explicit override.
            'mc_input.enabled': True,
            'mc_input.timeout_ms': 1000,
            # No camera.*/detect.* section exists in conversation.yaml at
            # all; defaults below match config/greeter.yaml.
            'camera.rgb_topic': '/aima/hal/sensor/rgbd_head_front/rgb_image',
            'camera.depth_topic': '/aima/hal/sensor/rgbd_head_front/depth_image',
            'camera.depth_scale': 0.001,
            'camera.max_sync_skew_s': 0.15,
            'camera.stale_warn_s': 5.0,
            'camera.rotate_180': False,
            'detect.detector': 'mobilenet_ssd',
            'detect.model_dir': '',
        }
        for name, default in defaults.items():
            self.declare_parameter(name, overrides.get(name, default))

    def _p(self, name: str):
        return self.get_parameter(name).value

    def _venue_path(self) -> Path:
        return (Path(get_package_share_directory('x2_greeter')) / 'config'
                / 'venues' / f"{self._p('venue.profile')}.yaml")

    def _conversation_limits(self) -> ConversationLimits:
        return ConversationLimits(
            max_turns=int(self._p('conversation.max_turns')),
            silence_timeout_s=float(self._p('conversation.silence_timeout_s')),
            session_max_s=float(self._p('conversation.session_max_s')),
            cooldown_s=float(self._p('conversation.cooldown_s')),
            backend_failures_max=int(self._p('conversation.backend_failures_max')),
            history_max=int(self._p('conversation.history_max')))

    # -- building the real adapters -----------------------------------------

    def _build_face(self) -> Face:
        return Face(self, service=self._p('face.service'),
                   enabled=bool(self._p('face.enabled')),
                   callback_group=self._service_group)

    def _build_head(self) -> Head:
        return Head(self, command_topic=self._p('head.command_topic'),
                   state_topic=self._p('head.state_topic'),
                   enabled=bool(self._p('head.enabled')),
                   rate_hz=float(self._p('head.rate_hz')),
                   callback_group=self._sensor_group)

    def _build_speech(self) -> '_SpeechAdapter':
        dispatcher = SpeechDispatcher(
            self, tier=self._p('speech.tier'), domain=self._p('speech.domain'),
            priority_level=int(self._p('speech.priority_level')),
            audio_dir=self._p('speech.audio_dir'),
            audio_file_count=int(self._p('speech.audio_file_count')),
            callback_group=self._service_group)
        return _SpeechAdapter(dispatcher)

    def _build_gestures(self) -> '_GestureGate':
        dispatcher = GestureDispatcher(self, callback_group=self._service_group)
        selector = GestureSelector(enabled=list(self._p('gestures.enabled')),
                                   hand_preference=self._p('gestures.hand_preference'))
        return _GestureGate(self, dispatcher, selector, self.mode_guard,
                            min_distance_m=float(self._p('safety.gesture_min_distance_m')),
                            stale_s=float(self._p('safety.stale_distance_s')))

    def _build_transcriber(self):
        try:
            return FasterWhisperTranscriber(
                model_size=self._p('hearing.model_size'),
                device=self._p('hearing.device'),
                compute_type=self._p('hearing.compute_type'),
                logger=_LoggerShim(self.get_logger()))
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().error(
                f'could not load the transcriber ({type(exc).__name__}: {exc}); '
                'every utterance will report as unavailable')
            return _NoTranscriber()

    def _build_backend(self):
        import os
        if not os.environ.get('ANTHROPIC_API_KEY'):
            self.get_logger().error(
                'ANTHROPIC_API_KEY is not set; the dialogue backend will refuse '
                'every turn')
            return _NoBackend()
        try:
            return ClaudeDialogueBackend(
                enabled_gestures=list(self._p('gestures.enabled')),
                enabled_emoji=list(self._p('face.enabled_expressions')),
                model=self._p('dialogue.model'), effort=self._p('dialogue.effort'),
                timeout_s=float(self._p('dialogue.timeout_s')),
                limits=TurnLimits(
                    reply_max_chars=int(self._p('conversation.reply_max_chars'))),
                logger=_LoggerShim(self.get_logger()))
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().error(
                f'could not build the dialogue backend ({type(exc).__name__}: '
                f'{exc}); it will refuse every turn')
            return _NoBackend()

    def _build_audio(self) -> Optional[VendorAudioSource]:
        if not bool(self._p('hearing.enabled')):
            return None
        return VendorAudioSource(
            self, on_utterance=self._on_utterance, topic=self._p('hearing.topic'),
            max_utterance_s=float(self._p('hearing.max_utterance_s')),
            callback_group=self._sensor_group)

    def _build_env_camera(self) -> Optional[EnvCamera]:
        if not bool(self._p('base_frame.use_env_camera')):
            return None
        return EnvCamera(
            self, topic=self._p('base_frame.rgb_topic'),
            on_frame=self._on_base_frame,
            rotate_180=bool(self._p('base_frame.rotate_180')),
            callback_group=self._sensor_group)

    def _detections(self, rgb):
        try:
            return self.detector.detect(rgb)
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self.get_logger().warning(
                f'detector raised {type(exc).__name__}: {exc}')
            return []

    # -- sensor callbacks (must not block) -----------------------------------

    def _on_frame(self, rgb, depth, at_s: float) -> None:
        """A head-camera frame. Kept as the newest; used on the next turn."""
        self._frame = rgb
        if self._base_frame is None and not self._p('base_frame.use_env_camera'):
            self._base_frame = rgb
        try:
            raws = self._detections(rgb)
            snapshot = observe(raws, rgb.shape, depth,
                               float(self._p('camera.depth_scale')),
                               self._scene_config, at_s)
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self.get_logger().warning(
                f'scene observation failed: {type(exc).__name__}: {exc}')
            return
        self._on_scene(snapshot, at_s)
        self._greeting_finished(at_s)

    def _on_base_frame(self, image, at_s: float) -> None:
        if self._base_frame is None:
            self._base_frame = image

    def _on_scene(self, snapshot, at_s: float) -> None:
        self._scene = snapshot
        if snapshot.subject is not None:
            self._latest_reading = (at_s, snapshot.subject.distance_m)
        with self._lock:
            state = self.conversation.state
            if state is SessionState.IDLE:
                if (snapshot.person_count == 0
                        or self.conversation.cooldown_active(at_s)):
                    return
                self._child = snapshot.has_child
                opening = self.conversation.start(snapshot, at_s)
                language = self.conversation.language
            else:
                self.conversation.observed(snapshot, at_s)
                return
        # Outside the lock: everything below talks to hardware.
        if (self._p('base_frame.use_env_camera') and self.env_camera is not None
                and self._base_frame is None):
            self.env_camera.capture_next()
        if self._p('head.sweep_on_start'):
            self.head.sweep()
        self.speech.say(opening, language=language)

    def _greeting_finished(self, at_s: float) -> None:
        """The opening line has finished playing (a no-op outside GREETING)."""
        with self._lock:
            self.conversation.greeted(at_s)

    def _on_utterance(self, pcm: bytes, at_s: float) -> None:
        """One VAD-segmented utterance. Handed off; never processed here."""
        try:
            self._queue.get_nowait()          # discard the oldest, if any
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait((pcm, at_s))
        except queue.Full:                    # noqa: BLE001 - lost a race; fine
            pass

    def _on_tick(self) -> None:
        at_s = self._now()
        with self._lock:
            self.conversation.tick(at_s)
            closed = self.conversation.state is SessionState.COOLDOWN
        if closed:
            self._after_close()

    # -- the worker thread ----------------------------------------------------

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                pcm, at_s = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle_utterance(pcm, at_s)
            except Exception as exc:               # noqa: BLE001 - never die
                self.get_logger().error(
                    f'turn handling failed: {type(exc).__name__}: {exc}')

    def _handle_utterance(self, pcm: bytes, at_s: float) -> None:
        with self._lock:
            if self.conversation.state is not SessionState.LISTENING:
                return
        self.face.show_thinking()
        try:
            utterance = self.transcriber.transcribe(pcm, SAMPLE_RATE_HZ)
        except TranscriptionUnavailable:
            self._say_fallback()
            return
        if utterance.is_empty:
            return

        with self._lock:
            self.conversation.heard(utterance, at_s)
            if self.conversation.state is not SessionState.THINKING:
                return
            history = self.conversation.history
            language = self.conversation.language
        scene = self._scene

        try:
            turn = self.backend.respond(
                base_frame=self._base_frame, frame=self._frame, scene=scene,
                venue=self.venue, history=history, utterance=utterance,
                language=language, child=self._child)
        except BackendUnavailable:
            with self._lock:
                fatal = self.conversation.backend_failed(self._now())
            self._say_fallback()
            if fatal:
                self._after_close()
            return

        with self._lock:
            self.conversation.replied(turn, self._now())

        if turn.gesture:
            self.gestures.play(turn.gesture)
        if turn.emoji:
            self.face.show(turn.emoji, MODE_ONCE)
        self.speech.say(turn.reply, language=turn.language)

        with self._lock:
            self.conversation.finished_speaking(self._now())
            closed = self.conversation.state is SessionState.COOLDOWN
        if closed:
            self._after_close()

    def _say_fallback(self) -> None:
        with self._lock:
            language = self.conversation.language
        self.speech.say(_FALLBACK_LINES.get(language, _FALLBACK_LINES['en']),
                        language=language)

    def _close(self, reason: 'CloseReason', at_s: float) -> None:
        with self._lock:
            self.conversation.close(reason, at_s)
            closed = self.conversation.state is SessionState.COOLDOWN
        if closed:
            self._after_close()

    def _after_close(self) -> None:
        self.face.clear()
        self._base_frame = None
        self._child = False

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    # -- shutdown -------------------------------------------------------------

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        if self.audio is not None:
            self.audio.stop()
        if self.env_camera is not None:
            self.env_camera.destroy()
        if self.frames is not None:
            self.frames.destroy()
        if getattr(self, 'input_source', None) is not None:
            try:
                self.input_source.deregister()
            except Exception as exc:                   # noqa: BLE001 - shutdown
                self.get_logger().warning(
                    f'could not release the input source: {type(exc).__name__}: {exc}')
        self.head.centre()
        self.head.destroy()

    def destroy_node(self) -> bool:
        try:
            self.shutdown()
        finally:
            return super().destroy_node()


# -- adapters that reconcile the real ros/ classes with this node's calls ---

class _SpeechAdapter:
    """say(text, language=...) over SpeechDispatcher.speak(text).

    SpeechDispatcher has no language parameter -- the reply text already
    carries the language and the vendor TTS is expected to read it as
    written -- so `language` is accepted only for interface parity with the
    unit tests' fakes and is otherwise unused.
    """

    def __init__(self, dispatcher: SpeechDispatcher) -> None:
        self._dispatcher = dispatcher

    def say(self, text: str, language: str = 'en') -> bool:
        return self._dispatcher.speak(text)


class _GestureGate:
    """name -> preset motion, behind the arm's-reach and motion-mode
    interlocks. Speaking never depends on this: every caller still says the
    reply whether or not the gesture was allowed.
    """

    def __init__(self, node, dispatcher: GestureDispatcher, selector: GestureSelector,
                mode_guard: ModeGuard, min_distance_m: float, stale_s: float) -> None:
        self._node = node
        self._dispatcher = dispatcher
        self._selector = selector
        self._mode_guard = mode_guard
        self._min_distance_m = float(min_distance_m)
        self._stale_s = float(stale_s)

    def play(self, name: Optional[str] = None) -> bool:
        if not self._allowed():
            return False
        choice = self._selector.select(name)
        return self._dispatcher.perform(choice.motion_id, choice.area_id)

    def _allowed(self) -> bool:
        if not self._mode_guard.gesturing_allowed():
            return False
        reading = self._node._latest_reading
        now = self._node._now()
        if reading is None or (now - reading[0]) > self._stale_s:
            self._node.get_logger().warning(
                'no fresh distance reading; not gesturing')
            return False
        if reading[1] < self._min_distance_m:
            self._node.get_logger().warning(
                f"subject at {reading[1]:.2f} m is inside the arm's-reach "
                'floor; not gesturing')
            return False
        return True


class _NoBackend:
    """Stands in for the dialogue backend when it could not be built.

    Every turn raises BackendUnavailable -- exactly the failure path the
    node already handles: it speaks a fallback line and counts the failure
    against conversation.backend_failures_max.
    """

    def respond(self, **kwargs):
        raise BackendUnavailable('no dialogue backend configured')


class _NoTranscriber:
    """Stands in for the transcriber when it could not be loaded."""

    def transcribe(self, pcm: bytes, sample_rate: int):
        raise TranscriptionUnavailable('no transcriber configured')


class _LoggerShim:
    """Lets plain-Python modules log through an rclpy logger.

    core/ and cognition/ take a logging.Logger-shaped object; rclpy's logger
    has the same method names but different formatting rules, so the shim
    pre-formats and forwards. Redefined here (rather than imported from
    ros/greeting_node.py) so this module never pulls in
    ros/interaction_guard.py as a side effect of that import.
    """

    def __init__(self, ros_logger) -> None:
        self._log = ros_logger

    def _emit(self, level, message, args):
        try:
            text = message % args if args else str(message)
        except Exception:                              # noqa: BLE001
            text = f'{message} {args}'
        getattr(self._log, level)(text)

    def debug(self, message, *args, **kwargs):
        self._emit('debug', message, args)

    def info(self, message, *args, **kwargs):
        self._emit('info', message, args)

    def warning(self, message, *args, **kwargs):
        self._emit('warning', message, args)

    warn = warning

    def error(self, message, *args, **kwargs):
        self._emit('error', message, args)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ConversationNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
