"""Encoding a camera frame for transmission.

The single place a frame is turned into bytes. Nothing here touches the
filesystem, and the caller receives bytes it holds only until the backend call
returns (spec section 12).
"""
from __future__ import annotations

import cv2
import numpy as np

from x2_greeter.core.types import JpegFrame


def to_jpeg_frame(bgr: np.ndarray, max_edge: int = 512, quality: int = 80) -> JpegFrame:
    """Downscale to `max_edge` on the longest side and JPEG-encode, in memory.

    512 px is enough for "is that a person, and roughly what are they doing?"
    while keeping the request small enough to fit the 2.5 s latency budget.
    """
    if bgr is None or bgr.size == 0:
        raise ValueError('cannot encode an empty frame')

    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)

    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest > max_edge:
        scale = max_edge / float(longest)
        bgr = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_AREA)

    ok, buffer = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError('JPEG encoding failed')
    return JpegFrame(data=buffer.tobytes())
