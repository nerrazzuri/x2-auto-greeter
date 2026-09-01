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
def spinning(ros):
    """A FakeRobot spinning on a background executor."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=True)
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot
    executor.shutdown()
    robot.destroy_node()
    thread.join(timeout=5.0)


def call(node, client, request, timeout_s=5.0):
    # add_done_callback alone never fires unless something spins `node`'s
    # executor; the client node here is never added to one, so we spin it
    # ourselves until the future resolves (or time out).
    import rclpy

    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_s)
    assert future.done(), 'service call timed out'
    return future.result()


def make_client(ros, srv_type, name):
    node = ros.create_node('test_client_' + name.rsplit('/', 1)[-1].lower())
    client = node.create_client(srv_type, name)
    assert client.wait_for_service(timeout_sec=10.0), f'{name} never appeared'
    return node, client


def test_it_serves_play_tts_and_records_the_request(spinning, ros):
    from aimdk_msgs.srv import PlayTts

    node, client = make_client(ros, PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
    try:
        req = PlayTts.Request()
        req.tts_req.text = 'Hello there!'
        req.tts_req.domain = 'x2_greeter'
        req.tts_req.priority_level.value = 6
        resp = call(node, client, req)
        assert resp.tts_resp.is_success is True
        assert spinning.tts_requests[0].tts_req.text == 'Hello there!'
    finally:
        node.destroy_node()


def test_tts_can_be_made_to_fail(spinning, ros):
    from aimdk_msgs.srv import PlayTts

    spinning.tts_succeeds = False
    node, client = make_client(ros, PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
    try:
        req = PlayTts.Request()
        req.tts_req.text = 'nope'
        assert call(node, client, req).tts_resp.is_success is False
    finally:
        node.destroy_node()


def test_it_serves_preset_motion(spinning, ros):
    from aimdk_msgs.msg import McControlArea, McPresetMotion
    from aimdk_msgs.srv import SetMcPresetMotion

    node, client = make_client(ros, SetMcPresetMotion, '/aimdk_5Fmsgs/srv/SetMcPresetMotion')
    try:
        req = SetMcPresetMotion.Request()
        motion = McPresetMotion()
        motion.value = 1002
        area = McControlArea()
        area.value = 2
        req.motion = motion
        req.area = area
        resp = call(node, client, req)
        assert resp.response.header.code == 0
        assert spinning.motion_requests[0].motion.value == 1002
        assert spinning.motion_requests[0].area.value == 2
    finally:
        node.destroy_node()


def test_it_reports_the_configured_motion_mode(spinning, ros):
    from aimdk_msgs.msg import McAction
    from aimdk_msgs.srv import GetMcAction

    node, client = make_client(ros, GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction')
    try:
        assert call(node, client, GetMcAction.Request()).info.current_action.value == \
            McAction.STAND_DEFAULT
        spinning.current_action = McAction.JOINT_DEFAULT
        assert call(node, client, GetMcAction.Request()).info.current_action.value == \
            McAction.JOINT_DEFAULT
        assert spinning.mode_queries >= 2
    finally:
        node.destroy_node()


def test_it_serves_play_audio_file(spinning, ros):
    from aimdk_msgs.srv import PlayAudioFile

    node, client = make_client(ros, PlayAudioFile, '/aimdk_5Fmsgs/srv/PlayAudioFile')
    try:
        req = PlayAudioFile.Request()
        req.file.pkg_name = 'x2_greeter'
        req.file.file_name = 'greeting_00.wav'
        req.file.file_path = '/var/tmp/x2_greeter_audio'
        resp = call(node, client, req)
        # The vendor .srv spells the response field 'reponse' (sic).
        assert resp.reponse.header.code == 0
        assert spinning.audio_requests[0].file.file_name == 'greeting_00.wav'
    finally:
        node.destroy_node()


def test_it_publishes_synchronised_rgb_and_depth(spinning, ros):
    from cv_bridge import CvBridge
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image

    received = {}
    node = ros.create_node('test_camera_sub')
    bridge = CvBridge()
    got = threading.Event()

    def on_rgb(msg):
        received['rgb'] = bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        if 'depth' in received:
            got.set()

    def on_depth(msg):
        received['depth'] = bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        if 'rgb' in received:
            got.set()

    node.create_subscription(Image, '/aima/hal/sensor/rgbd_head_front/rgb_image',
                             on_rgb, qos_profile_sensor_data)
    node.create_subscription(Image, '/aima/hal/sensor/rgbd_head_front/depth_image',
                             on_depth, qos_profile_sensor_data)
    executor_thread = threading.Thread(
        target=lambda: [ros.spin_once(node, timeout_sec=0.1) for _ in range(100)],
        daemon=True)
    executor_thread.start()
    try:
        assert got.wait(15.0), 'no camera frames arrived'
        assert received['rgb'].shape == (480, 640, 3)
        assert received['depth'].shape == (480, 640)
        assert int(received['depth'][240, 320]) == 2000
    finally:
        executor_thread.join(timeout=5.0)
        node.destroy_node()
