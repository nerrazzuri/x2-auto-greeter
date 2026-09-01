import cv2
import numpy as np
import pytest

from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.types import JpegFrame


def frame(height=480, width=640):
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def test_returns_a_jpeg_frame():
    result = to_jpeg_frame(frame())
    assert isinstance(result, JpegFrame)
    assert result.media_type == 'image/jpeg'
    assert result.data[:2] == b'\xff\xd8'  # JPEG SOI marker


def test_downscales_the_longest_edge():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(480, 640)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == 512
    assert decoded.shape[:2] == (384, 512)  # aspect ratio preserved


def test_downscales_a_portrait_frame_by_its_height():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(1200, 600)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (512, 256)


def test_a_small_frame_is_not_upscaled():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(200, 300)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (200, 300)


def test_a_grayscale_frame_is_promoted_to_three_channels():
    gray = np.zeros((480, 640), dtype=np.uint8)
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(gray).data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[2] == 3


def test_an_empty_frame_raises_rather_than_sending_garbage():
    with pytest.raises(ValueError, match='empty'):
        to_jpeg_frame(np.zeros((0, 0, 3), dtype=np.uint8))
