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
import time
from typing import Callable, Optional

DEFAULT_ATTEMPTS = 8
DEFAULT_TIMEOUT_S = 0.25


def await_service(client, budget_s: float, step_s: float) -> bool:
    """Wait for a service to be discovered. True if it is (or if we cannot tell).

    A client created moments ago has not discovered a cross-host service yet,
    and call_async on an undiscovered service returns a future that never
    completes -- so the retry loop below burns its whole budget and reports
    "did not respond" about a service that is running and healthy.

    Measured on an X2: SetMcInputSource answered 0 of 8 attempts from a cold
    client, and the same service answered 15 of 15 in a median of 3 ms once
    the client had waited for it. The vendor's own examples retry the call and
    never wait for the service; ros/agent_mode.py waits and has always worked.

    A client that does not offer wait_for_service (a test double) is treated
    as ready rather than as missing.
    """
    wait = getattr(client, 'wait_for_service', None)
    if wait is None:
        return True
    deadline = time.monotonic() + max(0.0, budget_s)
    while True:
        if wait(timeout_sec=step_s):
            return True
        if time.monotonic() >= deadline:
            return False


def wait_for_future(future, timeout_s: float) -> bool:
    """Block until `future` completes or `timeout_s` elapses. True if it completed."""
    done = threading.Event()
    future.add_done_callback(lambda _future: done.set())
    return done.wait(timeout_s)


def call_with_retry(client, request, *,
                    before_attempt: Optional[Callable[[object], None]] = None,
                    attempts: int = DEFAULT_ATTEMPTS,
                    timeout_s: float = DEFAULT_TIMEOUT_S,
                    discovery_s: Optional[float] = None,
                    logger=None):
    """Call an rclpy service client, retrying dropped attempts.

    Waits for the service to be discovered first -- see await_service; without
    that the retries are spent against a service the client has not found yet.
    `before_attempt` refreshes the request header's timestamp before each try,
    as the SDK examples do. Returns the response, or None if the service never
    appeared or every attempt was dropped.
    """
    if discovery_s is None:
        discovery_s = attempts * timeout_s
    if not await_service(client, discovery_s, timeout_s):
        if logger is not None:
            logger.warning(
                f'service not discovered within {discovery_s:.1f}s; not called')
        return None

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
