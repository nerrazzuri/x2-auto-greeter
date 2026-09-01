"""Calling an AimDK service from a thread while the executor is already spinning.

The SDK examples wrap every service call in eight attempts with a 0.25 s
timeout, with the comment "retry as remote peer is NOT handled well by ROS" —
the robot's services live on other compute units, and the first attempt is
routinely dropped. We keep those semantics exactly.

What we do not keep is rclpy.spin_until_future_complete: the examples call it
from main(), where nothing else is spinning the node. Our dispatchers run on a
worker thread while a MultiThreadedExecutor spins, so spinning again would
re-enter the executor. Waiting on an Event armed by add_done_callback gets the
same behaviour with none of the risk.

This module deliberately imports no rclpy, so it is unit-testable anywhere.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

DEFAULT_ATTEMPTS = 8
DEFAULT_TIMEOUT_S = 0.25


def wait_for_future(future, timeout_s: float) -> bool:
    """Block until `future` completes or `timeout_s` elapses. True if it completed."""
    done = threading.Event()
    future.add_done_callback(lambda _future: done.set())
    return done.wait(timeout_s)


def call_with_retry(client, request, *,
                    before_attempt: Optional[Callable[[object], None]] = None,
                    attempts: int = DEFAULT_ATTEMPTS,
                    timeout_s: float = DEFAULT_TIMEOUT_S,
                    logger=None):
    """Call an rclpy service client, retrying dropped attempts.

    `before_attempt` refreshes the request header's timestamp before each try,
    as the SDK examples do. Returns the response, or None if every attempt was
    dropped.
    """
    for attempt in range(attempts):
        if before_attempt is not None:
            before_attempt(request)
        future = client.call_async(request)
        if wait_for_future(future, timeout_s):
            return future.result()
        future.cancel()
        if logger is not None:
            logger.debug(f'service call attempt {attempt + 1}/{attempts} dropped, retrying')
    if logger is not None:
        logger.warning(f'service call failed after {attempts} attempts')
    return None
