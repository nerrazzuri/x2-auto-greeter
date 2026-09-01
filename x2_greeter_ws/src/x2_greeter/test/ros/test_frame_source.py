import threading

import numpy as np
import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def robot_and_consumer(ros):
    """A FakeRobot publishing frames, and a node consuming them via FrameSource."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.ros.frame_source import FrameSource
    from x2_greeter.sim.fake_robot import DEFAULT_DEPTH_TOPIC, DEFAULT_RGB_TOPIC, FakeRobot

    robot = FakeRobot(publish_camera=True)
    consumer = ros.create_node('frame_consumer')
    frames = []
    arrived = threading.Event()

    def on_frame(bgr, depth, stamp):
        frames.append((bgr, depth, stamp))
        arrived.set()

    source = FrameSource(consumer, DEFAULT_RGB_TOPIC, DEFAULT_DEPTH_TOPIC, on_frame)

    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield source, frames, arrived
    executor.shutdown()
    source.destroy()
    consumer.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def test_it_delivers_a_synchronised_rgb_and_depth_pair(robot_and_consumer):
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0), 'no frame delivered'
    bgr, depth, stamp = frames[0]
    assert bgr.shape == (480, 640, 3)
    assert bgr.dtype == np.uint8
    assert depth is not None
    assert depth.shape == (480, 640)
    assert int(depth[240, 320]) == 2000
    assert stamp > 0.0


def test_it_counts_the_frames_it_has_seen(robot_and_consumer):
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0)
    assert source.frames_seen >= 1


def test_it_keeps_delivering(robot_and_consumer):
    import time
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0)
    deadline = time.monotonic() + 10.0
    while len(frames) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(frames) >= 3


def test_it_delivers_nothing_when_only_rgb_is_published(ros):
    """Depth is required: an unpaired RGB frame must not be passed on."""
    import time

    from rclpy.executors import MultiThreadedExecutor
    from rclpy.qos import qos_profile_sensor_data
    from cv_bridge import CvBridge
    from sensor_msgs.msg import Image

    from x2_greeter.ros.frame_source import FrameSource

    publisher_node = ros.create_node('rgb_only_publisher')
    consumer = ros.create_node('rgb_only_consumer')
    frames = []
    FrameSource(consumer, '/test/rgb_only', '/test/depth_never', lambda *a: frames.append(a))
    pub = publisher_node.create_publisher(Image, '/test/rgb_only', qos_profile_sensor_data)

    executor = MultiThreadedExecutor()
    executor.add_node(publisher_node)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        bridge = CvBridge()
        for _ in range(5):
            msg = bridge.cv2_to_imgmsg(np.zeros((48, 64, 3), dtype=np.uint8), encoding='bgr8')
            msg.header.stamp = publisher_node.get_clock().now().to_msg()
            pub.publish(msg)
            time.sleep(0.1)
        time.sleep(0.5)
        assert frames == []
    finally:
        executor.shutdown()
        consumer.destroy_node()
        publisher_node.destroy_node()
        thread.join(timeout=5.0)
