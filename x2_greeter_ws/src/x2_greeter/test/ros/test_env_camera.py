"""The wide base frame, captured once per session on request.

Imports of sensor_msgs/x2_greeter.ros are deferred into functions rather
than done at module scope, matching every other file in test/ros/ --
sensor_msgs is only installed in the Docker harness, and a module-level
import would turn "deselected on host" into a collection error.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.ros


def _image(value=7, height=4, width=6):
    from sensor_msgs.msg import Image

    msg = Image()
    msg.height = height
    msg.width = width
    msg.encoding = 'bgr8'
    msg.step = width * 3
    msg.data = (np.full((height, width, 3), value, dtype=np.uint8)
                .tobytes())
    return msg


def _camera(**kwargs):
    from x2_greeter.ros.env_camera import EnvCamera
    return EnvCamera(**kwargs)


class _FakeNode:
    def __init__(self):
        self.callback = None
        self.logs = []

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.callback = callback
        self.topic = topic
        return object()

    def destroy_subscription(self, sub):
        self.destroyed = True

    def get_clock(self):
        class _C:
            def now(self):
                class _T:
                    nanoseconds = 0
                return _T()
        return _C()

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def test_nothing_is_delivered_until_a_capture_is_requested():
    node = _FakeNode()
    got = []
    _camera(node=node, topic='/env', on_frame=lambda img, t: got.append(img))
    node.callback(_image())
    assert got == [], (
        'the base frame is captured once per session, not every frame -- '
        'holding every wide frame would cost memory for nothing')


def test_capture_next_takes_exactly_one_frame():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    node.callback(_image(value=3))
    node.callback(_image(value=9))
    assert len(got) == 1
    assert int(got[0][0, 0, 0]) == 3


def test_the_delivered_frame_is_a_real_bgr_array():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    node.callback(_image(height=4, width=6))
    assert got[0].shape == (4, 6, 3)
    assert got[0].dtype == np.uint8


def test_rotation_is_applied_when_the_camera_is_mounted_upside_down():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env', rotate_180=True,
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    msg = _image(height=2, width=2)
    msg.data = np.array([[[1, 1, 1], [2, 2, 2]],
                         [[3, 3, 3], [4, 4, 4]]], dtype=np.uint8).tobytes()
    node.callback(msg)
    assert int(got[0][0, 0, 0]) == 4


def test_frames_seen_counts_everything_including_the_ones_dropped():
    node = _FakeNode()
    camera = _camera(node=node, topic='/env', on_frame=lambda img, t: None)
    for _ in range(3):
        node.callback(_image())
    assert camera.frames_seen == 3


def test_a_malformed_frame_is_dropped_without_taking_the_node_down():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    bad = _image()
    bad.data = b'\x00\x00'          # far too short for its declared shape
    node.callback(bad)
    assert got == []
    assert camera.pending is True, 'still waiting for a usable frame'


# ------------------------------------------------------- channel order (SHOULD FIX 4)

def test_an_rgb8_frame_is_converted_to_bgr():
    """core.imaging.to_jpeg_frame() -- where this image goes on its way to
    the model -- assumes BGR, and the other producer (frame_source.py) makes
    that true by asking CvBridge for desired_encoding='bgr8'. This one
    reshapes the buffer itself, so it has to honour msg.encoding by hand.
    Handing an rgb8 buffer straight through has the robot describe a blue
    shirt as orange.
    """
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    msg = _image(height=1, width=1)
    msg.encoding = 'rgb8'
    msg.data = np.array([[[10, 20, 30]]], dtype=np.uint8).tobytes()   # R,G,B
    node.callback(msg)
    assert len(got) == 1
    assert tuple(int(v) for v in got[0][0, 0]) == (30, 20, 10), (
        'red and blue must be swapped on the way out, not on the way to the '
        'model')


def test_a_bgr8_frame_is_passed_through_untouched():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    msg = _image(height=1, width=1)
    msg.encoding = 'bgr8'
    msg.data = np.array([[[10, 20, 30]]], dtype=np.uint8).tobytes()
    node.callback(msg)
    assert tuple(int(v) for v in got[0][0, 0]) == (10, 20, 30)


def test_an_encoding_this_camera_cannot_place_is_dropped_not_guessed_at():
    node = _FakeNode()
    got = []
    camera = _camera(node=node, topic='/env',
                     on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    msg = _image(height=1, width=1)
    msg.encoding = 'yuv422'
    node.callback(msg)
    assert got == []
    assert camera.pending is True, 'still waiting for a usable frame'
    assert any('yuv422' in str(line) for line in node.logs), (
        'a wrong channel order is silent; a dropped frame has to say so')
