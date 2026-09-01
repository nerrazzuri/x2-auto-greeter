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
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from x2_greeter.cognition.canned import DEFAULT_PHRASES, CannedBackend, load_phrases
from x2_greeter.cognition.policy import GreetingPolicy
from x2_greeter.core.detection import GateConfig, gate_detections
from x2_greeter.core.detectors import build_detector
from x2_greeter.core.gestures import DEFAULT_ENABLED, GestureSelector
from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.presence import PresenceConfig, PresenceState, PresenceTracker
from x2_greeter.core.types import SceneContext
from x2_greeter.ros.frame_source import FrameSource
from x2_greeter.ros.gesture import GestureDispatcher
from x2_greeter.ros.mode_guard import ModeGuard
from x2_greeter.ros.speech import SpeechDispatcher

#: The floor for the distance reading the node has, not a guarantee about
#: where the greeted person actually is: gate_detections (core/detection.py)
#: returns the most central gated detection, not the nearest, so a second
#: person who stays gated further away can mask one who has stepped inside
#: this floor. It is deliberately independent of detect.distance_min_m, which
#: operators may lower to notice people who step closer -- but on the shipped
#: config the two are equal, so the distance comparison here is unreachable:
#: gate_detections has already rejected anything closer before this interlock
#: sees it. The protection that actually fires in that configuration is
#: staleness: a person who crosses inside this floor stops being gated, the
#: latest reading ages past presence.loss_grace_s, and the gesture is refused
#: on age. That makes presence.loss_grace_s load-bearing for this interlock,
#: not just a responsiveness knob -- raising it weakens the arm's-reach
#: protection under the shipped config.
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
            audio_dir=self._param('speech.audio_dir'),
            audio_file_count=int(self._param('speech.audio_file_count')),
            rng=rng,
            callback_group=self._callback_group)
        self.gesture = GestureDispatcher(self, callback_group=self._callback_group)
        self.mode_guard = ModeGuard(
            self, require_stand_default=bool(self._param('safety.require_stand_default')),
            callback_group=self._callback_group)

        self._greeting_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='greeting')
        self._action_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gesture')
        self._greeting_future = None

        self.frames = FrameSource(
            self,
            rgb_topic=self._param('camera.rgb_topic'),
            depth_topic=self._param('camera.depth_topic'),
            on_frame=self._on_frame,
            max_sync_skew_s=self._param('camera.max_sync_skew_s'),
            stale_warn_s=self._param('camera.stale_warn_s'),
            callback_group=self._perception_group)

        self.get_logger().info(
            f'x2_greeter up: detector={type(self.detector).__name__} '
            f'backend={primary_name} gestures={len(self.selector.enabled_names)} '
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

        self.declare_parameter('detect.detector', 'mobilenet_ssd')
        self.declare_parameter('detect.model_dir', '')
        self.declare_parameter('detect.confidence_min', 0.5)
        self.declare_parameter('detect.distance_min_m', 1.0)
        self.declare_parameter('detect.distance_max_m', 3.0)
        self.declare_parameter('detect.center_tolerance', 0.25)

        self.declare_parameter('presence.dwell_s', 1.0)
        self.declare_parameter('presence.loss_grace_s', 0.5)
        self.declare_parameter('presence.clear_s', 3.0)
        self.declare_parameter('presence.cooldown_s', 30.0)
        self.declare_parameter('presence.reject_cooldown_s', 5.0)
        self.declare_parameter('presence.confirm_timeout_s', 10.0)

        self.declare_parameter('backend.provider', 'claude')
        self.declare_parameter('backend.model', 'claude-opus-5')
        self.declare_parameter('backend.effort', 'low')
        self.declare_parameter('backend.timeout_s', 2.5)

        self.declare_parameter('speech.tier', 'auto')
        self.declare_parameter('speech.domain', 'x2_greeter')
        self.declare_parameter('speech.priority_level', 6)
        self.declare_parameter('speech.audio_dir', '/var/tmp/x2_greeter_audio')
        self.declare_parameter('speech.audio_file_count', len(DEFAULT_PHRASES))
        self.declare_parameter('speech.phrases_file', '')

        self.declare_parameter('gestures.enabled', list(DEFAULT_ENABLED))
        self.declare_parameter('gestures.hand_preference', 'right')

        self.declare_parameter('safety.require_stand_default', True)

    def _param(self, name: str):
        return self.get_parameter(name).value

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

        now = self._now()
        with self._tracker_lock:
            if detection is not None:
                self._latest_reading = (now, detection.distance_m)
            to_confirm = self.tracker.update(now, detection)
        if to_confirm is None:
            return

        # A previous greeting is still in flight; the confirm timeout will
        # release the tracker if that worker never finishes.
        if self._greeting_future is not None and not self._greeting_future.done():
            self.get_logger().warning('greeting worker still busy; skipping this confirm')
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
            choice = self.selector.select(verdict.gesture)
            allowed = self.mode_guard.gesturing_allowed()
            if allowed:
                # This is the interlock itself, not belt and braces: it is a
                # fixed safety floor, deliberately independent of the tunable
                # detect.distance_min_m gate. ctx.distance_m was captured when
                # the tracker entered CONFIRMING, up to the full cloud budget
                # ago -- at walking pace someone can cross the floor in that
                # window, so this reads the freshest distance instead.
                now = self._now()
                with self._tracker_lock:
                    reading = self._latest_reading
                if reading is None or (now - reading[0]) > self._loss_grace_s:
                    age = f'{now - reading[0]:.2f}' if reading is not None else 'unknown'
                    self.get_logger().warning(
                        f'distance reading is {age}s old (bound {self._loss_grace_s:.2f}s); '
                        'not gesturing -- not knowing where the person is is not '
                        'knowing that it is safe')
                    allowed = False
                elif reading[1] < GESTURE_MIN_DISTANCE_M:
                    self.get_logger().warning(
                        f'person at {reading[1]:.2f} m is too close to gesture')
                    allowed = False

            self.get_logger().info(
                f'greeting ({verdict.source}): {verdict.greeting!r} + {choice.name}')

            gesture_future = None
            if allowed:
                gesture_future = self._action_pool.submit(
                    self.gesture.perform, choice.motion_id, choice.area_id)

            # Speech and gesture are independent: if one service is down, the
            # other still fires.
            try:
                self.speech.speak(verdict.greeting)
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

    def destroy_node(self) -> bool:
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
    rclpy.init(args=args)
    node = GreetingNode()
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
