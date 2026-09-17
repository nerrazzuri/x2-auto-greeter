"""The greeter.

Perception runs on the ROS executor; the cloud call and the greeting run on a
worker thread, so the camera loop never stalls behind a network request. The
gesture is dispatched on a second worker so speech and gesture happen at the
same time rather than one after the other.

The node never changes the robot's motion mode and never issues a locomotion
command.
"""
from __future__ import annotations

import os
import random
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.exceptions import ParameterUninitializedException
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions

from x2_greeter.cognition.canned import DEFAULT_PHRASES, CannedBackend, load_phrases
from x2_greeter.cognition.policy import GreetingPolicy
from x2_greeter.core.detection import GateConfig, gate_detections, person_distances
from x2_greeter.core.detectors import build_detector
from x2_greeter.core.gestures import DEFAULT_ENABLED, GestureSelector
from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.invitation import DEFAULT_INVITATIONS, append_invitation
from x2_greeter.core.presence import PresenceConfig, PresenceState, PresenceTracker
from x2_greeter.core.proximity import ProximityMonitor
from x2_greeter.core.types import SceneContext
from x2_greeter.ros.frame_source import FrameSource
from x2_greeter.ros.gesture import GestureDispatcher
from x2_greeter.ros.input_source import build_registrar
from x2_greeter.ros.interaction_guard import InteractionGuard
from x2_greeter.ros.mode_guard import LOCOMOTION, STAND, ModeGuard
from x2_greeter.ros.speech import SpeechDispatcher
from x2_greeter.ros.stereo_frame_source import StereoFrameSource

#: Nobody in view may be closer than this when an arm moves. Checked against
#: every confident person detection (core/proximity.py), not the greeting
#: target: gate_detections picks the most central person between
#: detect.distance_min_m and distance_max_m, so its reading says nothing about
#: somebody off to the side or already inside the gate's own minimum. Fixed,
#: and deliberately independent of detect.distance_min_m.
GESTURE_MIN_DISTANCE_M = 1.0


class GreetingNode(Node):
    def __init__(self, **node_kwargs) -> None:
        super().__init__('x2_greeter', **node_kwargs)
        self._finish_init()

    def _finish_init(self) -> None:
        """Everything after Node.__init__, so tests can construct with overrides."""
        self._declare_parameters()

        self._callback_group = ReentrantCallbackGroup()
        # Camera callbacks are serialised: the detector, the frame counter and
        # the tracker are all shared mutable state, and a reentrant group would
        # let two frames into detect() at once.
        self._perception_group = MutuallyExclusiveCallbackGroup()
        self._tracker_lock = threading.Lock()
        self._greetings_dispatched = 0
        # (timestamp, distance_m) of the freshest gated detection seen by
        # _on_frame, or None before the first one arrives. ctx.distance_m is
        # captured once, when the tracker enters CONFIRMING; by the time the
        # interlock in _greet runs, up to the full cloud budget may have
        # passed, so the interlock reads this instead. Guarded by
        # _tracker_lock, same as the tracker itself.
        self._latest_reading = None

        self._gate_config = GateConfig(
            confidence_min=self._param('detect.confidence_min'),
            distance_min_m=self._param('detect.distance_min_m'),
            distance_max_m=self._param('detect.distance_max_m'),
            center_tolerance=self._param('detect.center_tolerance'),
        )
        self._depth_scale = float(self._param('camera.depth_scale'))

        # Also the bound for how old a distance reading may be at the gesture
        # interlock: not knowing where the person is right now is not knowing
        # that it is safe to gesture, same reasoning as ModeGuard's refusal
        # when the mode service is unreachable.
        self._loss_grace_s = float(self._param('presence.loss_grace_s'))
        # Everyone in view over the same window, for the arm's-reach floor.
        # Guarded by _tracker_lock.
        self.proximity = ProximityMonitor(GESTURE_MIN_DISTANCE_M, self._loss_grace_s)
        self.tracker = PresenceTracker(PresenceConfig(
            dwell_s=self._param('presence.dwell_s'),
            loss_grace_s=self._loss_grace_s,
            clear_s=self._param('presence.clear_s'),
            cooldown_s=self._param('presence.cooldown_s'),
            reject_cooldown_s=self._param('presence.reject_cooldown_s'),
            confirm_timeout_s=self._param('presence.confirm_timeout_s'),
        ))

        self.detector = build_detector(
            self._param('detect.detector'), self._param('detect.model_dir'),
            logger=_LoggerShim(self.get_logger()))

        rng = random.Random()
        self._rng = rng
        self._invitations = tuple(self._param('speech.wake_invitations'))
        # Read through the guard: greeter.yaml ships this as `[]`, and an
        # empty sequence in a params file gives rclpy no element type to
        # infer, so the parameter stays uninitialised and get_parameter()
        # raises rather than returning []. Every site.yaml is seeded from
        # that file, so this killed the node during construction on the first
        # launch on a fresh robot (2026-09-11). Empty is the intended value --
        # no gestures while walking -- so an uninitialised read means exactly
        # that. See test/ros/test_params_file.py.
        self._walking_gestures = self._list_param('gestures.enabled_while_walking')
        self.selector = GestureSelector(
            enabled=list(self._param('gestures.enabled')),
            hand_preference=self._param('gestures.hand_preference'),
            rng=rng)

        primary, fallback, primary_name = self._build_backends(rng)
        self._primary_name = primary_name
        self._needs_frame = primary_name != 'canned'
        self.policy = GreetingPolicy(primary, fallback,
                                     logger=_LoggerShim(self.get_logger()))

        self.speech = SpeechDispatcher(
            self,
            tier=self._param('speech.tier'),
            domain=self._param('speech.domain'),
            priority_level=int(self._param('speech.priority_level')),
            tts_failures_before_demotion=int(
                self._param('speech.tts_failures_before_demotion')),
            audio_dir=self._param('speech.audio_dir'),
            audio_file_count=int(self._param('speech.audio_file_count')),
            rng=rng,
            callback_group=self._callback_group)
        # Built here, registered from _register_input_source once the executor
        # spins -- see build_registrar. Failure is logged, never fatal.
        self.input_source = build_registrar(
            self,
            enabled=bool(self._param('mc_input.enabled')),
            name=self._param('mc_input.name'),
            priority=int(self._param('mc_input.priority')),
            timeout_ms=int(self._param('mc_input.timeout_ms')),
            callback_group=self._callback_group)

        # The robot's own interaction system owns the same speaker. Greeting
        # over a conversation in progress is worse than not greeting at all.
        self.interaction = None
        if bool(self._param('interaction.respect_busy')):
            self.interaction = InteractionGuard(
                self,
                own_domain=self._param('speech.domain'),
                tts_status_topic=self._param('interaction.tts_status_topic'),
                play_state_topic=self._param('interaction.play_state_topic'),
                busy_timeout_s=float(self._param('interaction.busy_timeout_s')),
                callback_group=self._callback_group)

        self.gesture = GestureDispatcher(self, callback_group=self._callback_group)
        self.mode_guard = ModeGuard(
            self, require_stand_default=bool(self._param('safety.require_stand_default')),
            callback_group=self._callback_group)

        self._greeting_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='greeting')
        self._action_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gesture')
        self._greeting_future = None

        self._releasing = False
        self._registration_future = None
        self._startup_timer = None
        if self.input_source is not None:
            # A timer, because timers only fire once something spins the node.
            # Its own mutually exclusive group so it cannot run twice at once.
            self._startup_timer = self.create_timer(
                0.1, self._register_input_source,
                callback_group=MutuallyExclusiveCallbackGroup())

        source = str(self._param('camera.source')).strip().lower()
        if source == 'stereo':
            self.frames = StereoFrameSource(
                self,
                rgb_topic=self._param('camera.stereo_rgb_topic'),
                rgb_info_topic=self._param('camera.stereo_info_topic'),
                depth_topic=self._param('camera.depth_topic'),
                depth_info_topic=self._param('camera.depth_info_topic'),
                on_frame=self._on_frame,
                rgbd_xyz=list(self._param('camera.rgbd_xyz')),
                rgbd_rpy=list(self._param('camera.rgbd_rpy')),
                stereo_xyz=list(self._param('camera.stereo_xyz')),
                stereo_rpy=list(self._param('camera.stereo_rpy')),
                max_sync_skew_s=self._param('camera.max_sync_skew_s'),
                stale_warn_s=self._param('camera.stale_warn_s'),
                step=int(self._param('camera.reproject_step')),
                scale=int(self._param('camera.reproject_scale')),
                callback_group=self._perception_group)
        else:
            if source != 'rgbd':
                self.get_logger().error(
                    f'unknown camera.source {source!r}; using the chin RGB-D')
            self.frames = FrameSource(
                self,
                rgb_topic=self._param('camera.rgb_topic'),
                depth_topic=self._param('camera.depth_topic'),
                on_frame=self._on_frame,
                max_sync_skew_s=self._param('camera.max_sync_skew_s'),
                stale_warn_s=self._param('camera.stale_warn_s'),
                rotate_180=bool(self._param('camera.rotate_180')),
                callback_group=self._perception_group)

        self.get_logger().info(
            f'x2_greeter up: detector={type(self.detector).__name__} '
            f'camera={source} backend={primary_name} '
            f'gestures={len(self.selector.enabled_names)} '
            f"speech_tier={self._param('speech.tier')}")

    # ---------------------------------------------------------------- params

    def _declare_parameters(self) -> None:
        self.declare_parameter('camera.rgb_topic',
                               '/aima/hal/sensor/rgbd_head_front/rgb_image')
        self.declare_parameter('camera.depth_topic',
                               '/aima/hal/sensor/rgbd_head_front/depth_image')
        self.declare_parameter('camera.depth_scale', 0.001)
        self.declare_parameter('camera.max_sync_skew_s', 0.15)
        self.declare_parameter('camera.stale_warn_s', 5.0)
        self.declare_parameter('camera.rotate_180', False)
        self.declare_parameter('camera.source', 'rgbd')
        self.declare_parameter('camera.stereo_rgb_topic',
                               '/aima/hal/sensor/stereo_head_front_left/rgb_image')
        self.declare_parameter('camera.stereo_info_topic',
                               '/aima/hal/sensor/stereo_head_front_left/camera_info')
        self.declare_parameter('camera.depth_info_topic',
                               '/aima/hal/sensor/rgbd_head_front/depth_camera_info')
        self.declare_parameter('camera.rgbd_xyz', [0.05761, -0.011183, -0.04837])
        self.declare_parameter('camera.rgbd_rpy', [2.2689, 0.0, 1.5708])
        self.declare_parameter('camera.stereo_xyz', [0.067995, 0.029784, 0.05])
        self.declare_parameter('camera.stereo_rpy', [-1.5708, 0.0, -1.574])
        self.declare_parameter('camera.reproject_step', 8)
        self.declare_parameter('camera.reproject_scale', 4)

        self.declare_parameter('detect.detector', 'mobilenet_ssd')
        self.declare_parameter('detect.model_dir', '')
        self.declare_parameter('detect.confidence_min', 0.5)
        self.declare_parameter('detect.distance_min_m', 1.0)
        self.declare_parameter('detect.distance_max_m', 3.0)
        self.declare_parameter('detect.center_tolerance', 0.25)

        self.declare_parameter('presence.dwell_s', 1.0)
        self.declare_parameter('presence.loss_grace_s', 0.5)
        self.declare_parameter('presence.clear_s', 2.0)
        self.declare_parameter('presence.cooldown_s', 10.0)
        self.declare_parameter('presence.reject_cooldown_s', 5.0)
        self.declare_parameter('presence.confirm_timeout_s', 10.0)

        self.declare_parameter('interaction.respect_busy', True)
        self.declare_parameter('interaction.tts_status_topic',
                               '/interaction/tts_status')
        self.declare_parameter('interaction.play_state_topic',
                               '/aima/hal/audio/play_state')
        self.declare_parameter('interaction.busy_timeout_s', 15.0)

        self.declare_parameter('mc_input.enabled', True)
        self.declare_parameter('mc_input.name', 'x2_greeter')
        self.declare_parameter('mc_input.priority', 30)
        self.declare_parameter('mc_input.timeout_ms', 1000)

        self.declare_parameter('backend.provider', 'claude')
        self.declare_parameter('backend.model', 'claude-opus-5')
        self.declare_parameter('backend.effort', 'low')
        self.declare_parameter('backend.timeout_s', 2.5)

        self.declare_parameter('speech.tier', 'auto')
        self.declare_parameter('speech.domain', 'x2_greeter')
        self.declare_parameter('speech.priority_level', 6)
        self.declare_parameter('speech.tts_failures_before_demotion', 3)
        self.declare_parameter('speech.audio_dir', '/var/tmp/x2_greeter_audio')
        self.declare_parameter('speech.audio_file_count', len(DEFAULT_PHRASES))
        self.declare_parameter('speech.phrases_file', '')
        self.declare_parameter('speech.wake_invitations',
                               list(DEFAULT_INVITATIONS))

        self.declare_parameter('gestures.enabled', list(DEFAULT_ENABLED))
        self.declare_parameter('gestures.hand_preference', 'right')
        self.declare_parameter('gestures.enabled_while_walking', [])

        self.declare_parameter('safety.require_stand_default', True)

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _list_param(self, name: str) -> tuple:
        """A list parameter that may legitimately be empty, as a tuple.

        An empty list is not a missing value here, but rclpy cannot tell them
        apart: it infers a parameter's type from the value it is given, an
        empty YAML sequence carries no element type, and the parameter is left
        uninitialised. Treating that as the empty list is the only reading
        that matches what the file says.
        """
        try:
            value = self._param(name)
        except ParameterUninitializedException:
            return ()
        return tuple(value or ())

    # -------------------------------------------------------------- backends

    def _build_backends(self, rng):
        """Return (primary, fallback, primary_name), degrading rather than exiting."""
        phrases = self._load_phrases()
        canned = CannedBackend(phrases, rng=rng)

        provider = self._param('backend.provider')
        if provider == 'canned':
            self.get_logger().info('backend.provider=canned; greeting offline only')
            return canned, canned, 'canned'
        if provider != 'claude':
            self.get_logger().error(
                f'unknown backend.provider {provider!r}; using the canned backend')
            return canned, canned, 'canned'

        if not os.environ.get('ANTHROPIC_API_KEY'):
            self.get_logger().warning(
                'ANTHROPIC_API_KEY is not set; greeting from the canned phrase list only')
            return canned, canned, 'canned'

        try:
            from x2_greeter.cognition.claude import ClaudeBackend
            primary = ClaudeBackend(
                enabled_gestures=self.selector.enabled_names,
                model=self._param('backend.model'),
                effort=self._param('backend.effort'),
                timeout_s=self._param('backend.timeout_s'),
                logger=_LoggerShim(self.get_logger()))
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().error(
                f'could not start the Claude backend ({exc}); greeting from the canned '
                'phrase list only')
            return canned, canned, 'canned'
        return primary, canned, 'claude'

    def _load_phrases(self):
        path = self._param('speech.phrases_file')
        if not path:
            try:
                from ament_index_python.packages import get_package_share_directory
                path = os.path.join(get_package_share_directory('x2_greeter'),
                                    'config', 'phrases.yaml')
            except Exception:                          # noqa: BLE001 - not installed
                return DEFAULT_PHRASES
        try:
            return load_phrases(path)
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().warning(
                f'could not read {path} ({exc}); using the built-in phrases')
            return DEFAULT_PHRASES

    # ---------------------------------------------------------- perception

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_frame(self, bgr, depth, stamp: float) -> None:
        """Runs on a ROS executor thread. Must not block."""
        try:
            raws = self.detector.detect(bgr)
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self.get_logger().warning(f'detector raised {type(exc).__name__}: {exc}')
            return

        detection = gate_detections(raws, bgr.shape[:2], depth,
                                    self._depth_scale, self._gate_config)
        distances = person_distances(raws, bgr.shape[:2], depth, self._depth_scale,
                                     self._gate_config.confidence_min)

        now = self._now()
        with self._tracker_lock:
            self.proximity.observe(now, distances)
            if detection is not None:
                self._latest_reading = (now, detection.distance_m)
            to_confirm = self.tracker.update(now, detection)
        if to_confirm is None:
            return

        if self._releasing:
            return

        # A previous greeting is still in flight; the confirm timeout will
        # release the tracker if that worker never finishes.
        if self._greeting_future is not None and not self._greeting_future.done():
            self.get_logger().warning('greeting worker still busy; skipping this confirm')
            return

        # Not while the robot is mid-conversation. The tracker keeps the
        # person; the confirm timeout releases it if this keeps being true.
        if self.interaction is not None:
            reason = self.interaction.busy_reason()
            if reason is not None:
                self.get_logger().info(f'not greeting: {reason}')
                return

        try:
            frame = to_jpeg_frame(bgr) if self._needs_frame else None
            ctx = SceneContext(distance_m=to_confirm.distance_m,
                               center_offset=to_confirm.center_offset)
            self._greeting_future = self._greeting_pool.submit(self._greet, frame, ctx)
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self.get_logger().error(f'could not start the greeting worker: '
                                     f'{type(exc).__name__}: {exc}')
            return

    # ------------------------------------------------------------- greeting

    def _greet(self, frame, ctx: SceneContext) -> None:
        """Runs on the greeting worker. Blocking is fine here."""
        try:
            verdict = self.policy.compose(frame, ctx)
        finally:
            # Release the frame as early as possible: nothing else needs it and
            # it is never written anywhere (spec section 12).
            frame = None

        person_present = verdict.person_present
        if verdict.source != self._primary_name:
            # We fell back. The spec only allows a canned greeting while the
            # local detector still sees somebody.
            with self._tracker_lock:
                person_present = person_present and self.tracker.has_live_detection

        with self._tracker_lock:
            self.tracker.on_verdict(self._now(), person_present)
            greeting = self.tracker.state is PresenceState.GREETING
        if not greeting:
            self.get_logger().info(f'not greeting: {verdict.reason or "no person"}')
            return

        succeeded = False
        try:
            # Which gestures this motion mode permits. Standing: all of them.
            # Walking: only gestures.enabled_while_walking, which ships empty
            # -- the vendor documents no preset motion as safe during
            # locomotion, so every entry has to be one somebody validated on
            # hardware. Any other mode, or an unreadable one, refuses.
            mode = self.mode_guard.resolve()
            pool = None
            allowed = mode == STAND
            if mode == LOCOMOTION and self._walking_gestures:
                pool = self._walking_gestures
                allowed = True
                self.get_logger().info(
                    f'walking: gesturing from the walking list {list(pool)}')
            try:
                choice = self.selector.select(verdict.gesture, allowed=pool)
            except ValueError as exc:
                self.get_logger().warning(f'no gesture available here ({exc}); speaking only')
                allowed = False
                choice = self.selector.select(verdict.gesture)
            if allowed:
                # The interlock itself, not belt and braces. Two questions,
                # both answered from frames within loss_grace_s rather than
                # from ctx, which is up to the full cloud budget old.
                #
                # Is the greeted person still gated? Somebody who stepped
                # inside the gate's minimum, or out of view, stops refreshing
                # the reading.
                #
                # Is anybody at all inside the arm's-reach floor? Asked of
                # every person detected, so the greeted person cannot mask a
                # closer one.
                now = self._now()
                with self._tracker_lock:
                    reading = self._latest_reading
                    clearance = self.proximity.clearance(now)
                if reading is None or (now - reading[0]) > self._loss_grace_s:
                    age = f'{now - reading[0]:.2f}' if reading is not None else 'unknown'
                    self.get_logger().warning(
                        f'distance reading is {age}s old (bound {self._loss_grace_s:.2f}s); '
                        'not gesturing -- not knowing where the person is is not '
                        'knowing that it is safe')
                    allowed = False
                elif not clearance.clear:
                    self.get_logger().warning(f'not gesturing: {clearance.reason}')
                    allowed = False

            # The wake word is the only way into a conversation on this robot,
            # so every greeting ends by saying it. See core/invitation.py.
            spoken = append_invitation(verdict.greeting, self._invitations, self._rng)

            self.get_logger().info(
                f'greeting ({verdict.source}): {spoken!r} + {choice.name}')

            gesture_future = None
            if allowed:
                gesture_future = self._action_pool.submit(
                    self.gesture.perform, choice.motion_id, choice.area_id)

            # Speech and gesture are independent: if one service is down, the
            # other still fires.
            try:
                self.speech.speak(spoken)
            except Exception as exc:                   # noqa: BLE001
                self.get_logger().error(f'speech failed: {type(exc).__name__}: {exc}')

            if gesture_future is not None:
                try:
                    gesture_future.result(timeout=15.0)
                except Exception as exc:               # noqa: BLE001
                    self.get_logger().error(f'gesture failed: {type(exc).__name__}: {exc}')
            succeeded = True
        except Exception as exc:                       # noqa: BLE001 - never wedge the tracker
            self.get_logger().error(
                f'greeting worker failed: {type(exc).__name__}: {exc}')
        finally:
            with self._tracker_lock:
                self.tracker.on_greeting_dispatched(self._now())
        if succeeded:
            self._greetings_dispatched += 1

    # ----------------------------------------------------------------- misc

    @property
    def greetings_dispatched(self) -> int:
        return self._greetings_dispatched

    def wait_for_idle_worker(self, timeout_s: float) -> bool:
        """True once no greeting is in flight. Used by tests, not by the robot."""
        future = self._greeting_future
        if future is None:
            return True
        try:
            future.result(timeout=timeout_s)
            return True
        except FutureTimeoutError:
            return False

    # ------------------------------------------------------------- lifecycle

    def _register_input_source(self) -> None:
        """Runs once, from the startup timer, with the executor spinning."""
        self._startup_timer.cancel()
        if self._releasing or self._registration_future is not None:
            return
        # On the greeting worker rather than this executor thread: it blocks
        # for up to a few seconds, and a greeting confirmed in the meantime
        # queues behind it instead of gesturing before the source exists.
        self._registration_future = self._greeting_pool.submit(self.input_source.register)

    def release(self) -> None:
        """Stop greeting and give the input source back. Safe to call twice.

        Must run while the executor still spins: the DELETE is a service call,
        and after executor.shutdown() nothing can deliver its response.

        Does not wait for a greeting in flight. ros2 launch follows SIGINT with
        SIGTERM after 5 s and SIGKILL after 10 s, less than a cloud call plus a
        gesture, and the source must be back before that. A gesture issued
        after the DELETE comes from an unknown source, which the controller
        discards.
        """
        if self._releasing:
            return
        self._releasing = True
        if self._startup_timer is not None:
            self._startup_timer.cancel()
        self._greeting_pool.shutdown(wait=False, cancel_futures=True)
        self._action_pool.shutdown(wait=False, cancel_futures=True)
        if self.input_source is None:
            return
        registration = self._registration_future
        if registration is not None:
            try:
                registration.result(timeout=5.0)
            except Exception:                          # noqa: BLE001 - deregister decides
                pass
        # So the next run gets a clean ADD rather than the restart path.
        try:
            self.input_source.deregister()
        except Exception as exc:                       # noqa: BLE001 - shutdown
            self.get_logger().warning(
                f'could not release the input source: {type(exc).__name__}: {exc}')

    def destroy_node(self) -> bool:
        # Normally already done by main(), while the executor still spun.
        self.release()
        self._greeting_pool.shutdown(wait=True)
        self._action_pool.shutdown(wait=True)
        return super().destroy_node()


class _LoggerShim:
    """Lets plain-Python modules log through an rclpy logger.

    core/ and cognition/ take a logging.Logger-shaped object; rclpy's logger
    has the same method names but different formatting rules, so the shim
    pre-formats and forwards.
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
    # rclpy's own SIGINT/SIGTERM handlers shut the context down the moment the
    # signal lands, and a context that is shut down cannot make the DELETE that
    # gives the input source back. So the signals only ask main to stop, and
    # main releases the node while the executor is still spinning.
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    node = GreetingNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, name='executor', daemon=True)
    spinner.start()
    try:
        # A timed wait, so the signal handler gets to run on this thread.
        while spinner.is_alive() and not stop.wait(0.5):
            pass
        node.release()
    finally:
        executor.shutdown()
        node.destroy_node()
        spinner.join(timeout=5.0)
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
