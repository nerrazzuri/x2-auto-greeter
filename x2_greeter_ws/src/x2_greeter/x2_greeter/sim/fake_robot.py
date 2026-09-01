"""A stand-in for the X2, so the greeter can be developed without hardware.

Serves the four services the greeter calls and publishes a synthetic RGB-D
stream. Every request is recorded so a test can assert not only that the robot
was told to do something, but exactly what.
"""
from __future__ import annotations

import numpy as np
import rclpy
from aimdk_msgs.msg import CommonState, McAction
from aimdk_msgs.srv import GetMcAction, PlayAudioFile, PlayTts, SetMcPresetMotion
from cv_bridge import CvBridge
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

DEFAULT_RGB_TOPIC = '/aima/hal/sensor/rgbd_head_front/rgb_image'
DEFAULT_DEPTH_TOPIC = '/aima/hal/sensor/rgbd_head_front/depth_image'


class FakeRobot(Node):
    def __init__(self, rgb_topic: str = DEFAULT_RGB_TOPIC,
                 depth_topic: str = DEFAULT_DEPTH_TOPIC,
                 publish_camera: bool = True,
                 current_action: int = McAction.STAND_DEFAULT,
                 tts_succeeds: bool = True,
                 distance_mm: int = 2000,
                 width: int = 640, height: int = 480,
                 rate_hz: float = 10.0) -> None:
        super().__init__('fake_robot')

        self.tts_requests = []
        self.audio_requests = []
        self.motion_requests = []
        self.mode_queries = 0
        self.current_action = current_action
        self.tts_succeeds = tts_succeeds

        group = ReentrantCallbackGroup()
        self.create_service(PlayTts, '/aimdk_5Fmsgs/srv/PlayTts',
                            self._on_play_tts, callback_group=group)
        self.create_service(PlayAudioFile, '/aimdk_5Fmsgs/srv/PlayAudioFile',
                            self._on_play_audio_file, callback_group=group)
        self.create_service(SetMcPresetMotion, '/aimdk_5Fmsgs/srv/SetMcPresetMotion',
                            self._on_preset_motion, callback_group=group)
        self.create_service(GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction',
                            self._on_get_action, callback_group=group)

        self._bridge = CvBridge()
        self._width = width
        self._height = height
        if publish_camera:
            self._rgb_pub = self.create_publisher(Image, rgb_topic, qos_profile_sensor_data)
            self._depth_pub = self.create_publisher(Image, depth_topic, qos_profile_sensor_data)
            self._rgb = self._make_rgb(width, height)
            self._depth = np.full((height, width), distance_mm, dtype=np.uint16)
            self.create_timer(1.0 / rate_hz, self._publish_frame, callback_group=group)

        self.get_logger().info(
            f"fake robot up: 4 services, camera {'publishing' if publish_camera else 'off'}")

    @staticmethod
    def _make_rgb(width: int, height: int) -> np.ndarray:
        """A recognisable but meaningless frame: a gradient with a lighter slab.

        Deliberately not a picture of a person — the integration test drives
        detection with ScriptedDetector, so the pixels only have to be valid.
        """
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
        frame[:, :, 1] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
        frame[height // 4:, width // 2 - 60:width // 2 + 60, 2] = 200
        return frame

    def _publish_frame(self) -> None:
        stamp = self.get_clock().now().to_msg()
        rgb_msg = self._bridge.cv2_to_imgmsg(self._rgb, encoding='bgr8')
        depth_msg = self._bridge.cv2_to_imgmsg(self._depth, encoding='16UC1')
        for msg in (rgb_msg, depth_msg):
            msg.header.stamp = stamp
            msg.header.frame_id = 'rgbd_head_front'
        self._rgb_pub.publish(rgb_msg)
        self._depth_pub.publish(depth_msg)

    def _on_play_tts(self, request, response):
        self.tts_requests.append(request)
        self.get_logger().info(f'TTS: {request.tts_req.text!r}')
        response.header.header.code = 0
        response.tts_resp.is_success = bool(self.tts_succeeds)
        response.tts_resp.text = request.tts_req.text
        response.tts_resp.domain = request.tts_req.domain
        if not self.tts_succeeds:
            response.tts_resp.error_message = 'fake robot: TTS disabled'
        return response

    def _on_play_audio_file(self, request, response):
        self.audio_requests.append(request)
        self.get_logger().info(
            f'audio file: {request.file.file_path}/{request.file.file_name}')
        # The vendor .srv spells the response field 'reponse' (sic). The real
        # answer lives in `status`, per CommonResponse -- a header-only
        # response (header.code == 0, status left at UNKNOWN) is also treated
        # as success by the dispatcher, but the fake reports the way the
        # service is documented to: status.value == SUCCESS.
        response.reponse.header.code = 0
        response.reponse.status.value = CommonState.SUCCESS
        return response

    def _on_preset_motion(self, request, response):
        self.motion_requests.append(request)
        self.get_logger().info(
            f'preset motion {request.motion.value} on area {request.area.value}')
        # CommonTaskResponse's real answer lives in `state`, not header.code.
        response.response.header.code = 0
        response.response.state.value = CommonState.SUCCESS
        response.response.task_id = len(self.motion_requests)
        return response

    def _on_get_action(self, request, response):
        self.mode_queries += 1
        response.header.code = 0
        response.info.current_action.value = int(self.current_action)
        response.info.action_desc = 'fake'
        return response


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FakeRobot()
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
