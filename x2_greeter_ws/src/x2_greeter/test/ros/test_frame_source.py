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


# ------------------------------------------------------- upside-down mounting

def _asymmetric_pair():
    """An RGB/depth pair whose corners are all distinguishable."""
    import numpy as np
    bgr = np.zeros((8, 10, 3), dtype=np.uint8)
    bgr[0, 0] = (10, 20, 30)      # top-left
    bgr[7, 9] = (40, 50, 60)      # bottom-right
    depth = np.zeros((8, 10), dtype=np.uint16)
    depth[0, 0] = 1000
    depth[7, 9] = 2000
    return bgr, depth


def test_rotate_180_turns_the_frame_half_a_turn():
    import numpy as np
    from x2_greeter.ros.frame_source import rotate_180

    bgr, _ = _asymmetric_pair()
    turned = rotate_180(bgr)

    assert tuple(turned[7, 9]) == (10, 20, 30)   # old top-left is now bottom-right
    assert tuple(turned[0, 0]) == (40, 50, 60)
    assert turned.shape == bgr.shape
    # cv2.dnn reads the buffer directly; a reversed view would not do.
    assert turned.flags['C_CONTIGUOUS']
    # Rotating twice is the identity -- i.e. it is a rotation, not a flip.
    assert np.array_equal(rotate_180(turned), bgr)


def test_rotate_180_does_not_touch_the_colour_channels():
    # Reversing the channel axis as well would silently swap BGR for RGB on
    # top of the rotation, and the detector would quietly get worse rather
    # than fail.
    from x2_greeter.ros.frame_source import rotate_180

    bgr, _ = _asymmetric_pair()
    assert tuple(rotate_180(bgr)[7, 9]) == (10, 20, 30)   # not (30, 20, 10)


def test_rotate_180_handles_a_single_channel_depth_image():
    from x2_greeter.ros.frame_source import rotate_180

    _, depth = _asymmetric_pair()
    turned = rotate_180(depth)

    assert turned[7, 9] == 1000
    assert turned[0, 0] == 2000
    assert turned.shape == depth.shape


def test_rotate_180_turns_rgb_and_depth_together(ros):
    """The safety-critical half: a box found in the rotated colour image is
    read against the depth array, so the two must be turned as a pair. If only
    one were rotated, every distance lookup would land on the diagonally
    opposite corner -- and that distance is the arm's-reach interlock's input.
    """
    import threading

    from cv_bridge import CvBridge
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image

    from x2_greeter.ros.frame_source import FrameSource

    RGB_TOPIC, DEPTH_TOPIC = '/test/rot/rgb', '/test/rot/depth'
    bridge = CvBridge()

    # Colour marks the top-left corner; depth marks the same corner. After a
    # half turn both must appear bottom-right.
    bgr = np.zeros((8, 10, 3), dtype=np.uint8)
    bgr[0, 0] = (10, 20, 30)
    depth = np.full((8, 10), 500, dtype=np.uint16)
    depth[0, 0] = 1234

    publisher = ros.create_node('rot_publisher')
    rgb_pub = publisher.create_publisher(Image, RGB_TOPIC, qos_profile_sensor_data)
    depth_pub = publisher.create_publisher(Image, DEPTH_TOPIC, qos_profile_sensor_data)

    consumer = ros.create_node('rot_consumer')
    got = []
    arrived = threading.Event()

    def on_frame(b, d, stamp):
        got.append((b, d))
        arrived.set()

    source = FrameSource(consumer, RGB_TOPIC, DEPTH_TOPIC, on_frame, rotate_180=True)

    def publish():
        stamp = publisher.get_clock().now().to_msg()
        rgb_msg = bridge.cv2_to_imgmsg(bgr, encoding='bgr8')
        depth_msg = bridge.cv2_to_imgmsg(depth, encoding='16UC1')
        for msg in (rgb_msg, depth_msg):
            msg.header.stamp = stamp
        depth_pub.publish(depth_msg)   # depth first: it is cached, not synced
        rgb_pub.publish(rgb_msg)

    publisher.create_timer(0.1, publish)

    executor = MultiThreadedExecutor()
    executor.add_node(publisher)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert arrived.wait(15.0), 'no frame delivered'
        out_bgr, out_depth = got[0]
        assert tuple(out_bgr[7, 9]) == (10, 20, 30), 'colour frame not rotated'
        assert out_depth[7, 9] == 1234, 'depth frame not rotated with the colour frame'
        assert out_depth[0, 0] == 500
    finally:
        executor.shutdown()
        source.destroy()
        consumer.destroy_node()
        publisher.destroy_node()
        thread.join(timeout=5.0)
