"""Watching for the robot, so nobody has to press a button to look for it.

A "Find robot" button asks the customer to guess when the answer might have
changed. They plug the cable in, press it, get told the address is wrong, fix
the address, and have to remember to press it again. Every one of those steps
is a place to give up.

So this polls instead, and reports one of four states. They are ordered by how
far the customer has got, and each names the next thing to do:

    no_link      no network cable, or the wired adapter is off
    wrong_subnet the adapter is up but not on the robot's network
    no_ssh       on the right network, but the robot is not answering
    ready        the robot answered, and said which unit it is

The polling is cheap -- a UDP socket that sends nothing, and a TCP connect --
except in `ready`, where it stops asking. The expensive one is SSH, and that
is attempted only once the network is right.

What this does not do is write to the log. Polling every two seconds and
logging every time would bury the deployment's own output within a minute, so
the caller is told whether a line is worth printing, and the rule lives in
`Throttle` where it can be tested.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from . import network
from .i18n import t
from .robot import DEFAULT_HOST, DEFAULT_PASSWORD, DEFAULT_USER, Identity, Robot

NO_LINK = 'no_link'
WRONG_SUBNET = 'wrong_subnet'
NO_SSH = 'no_ssh'
READY = 'ready'

POLL_S = 2.0            # how often to look
REPEAT_S = 10.0         # how often the same news is worth repeating


@dataclass(frozen=True)
class Status:
    state: str
    detail: str
    identity: Optional[Identity] = None

    @property
    def ready(self) -> bool:
        return self.state == READY


class Throttle:
    """Says whether a status is worth putting in the log yet.

    A change is always worth it -- the customer just did something, and seeing
    the result immediately is the whole point. The same news repeats at most
    every REPEAT_S, so that a cable left unplugged for ten minutes costs six
    lines rather than three hundred.
    """

    def __init__(self, repeat_s: float = REPEAT_S) -> None:
        self._repeat_s = repeat_s
        self._last_state: Optional[str] = None
        self._last_at = 0.0

    def should_log(self, status: Status, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if status.state != self._last_state:
            self._last_state, self._last_at = status.state, now
            return True
        if now - self._last_at >= self._repeat_s:
            self._last_at = now
            return True
        return False


class Watcher:
    """One poll of the robot's reachability, from the cable up."""

    def __init__(self, host: str = DEFAULT_HOST, user: str = DEFAULT_USER,
                 password: str = DEFAULT_PASSWORD) -> None:
        self.host, self.user, self.password = host, user, password

    def poll(self) -> Status:
        local = network.route_address(self.host)
        if local is None:
            return Status(NO_LINK, t('watch.no_link'))

        if not network.same_subnet(local, self.host):
            return Status(
                WRONG_SUBNET,
                t('watch.wrong_subnet', address=local, host=self.host) + '\n'
                + network.fix_instructions(self.host))

        if not network.port_open(self.host, timeout=2.0):
            return Status(NO_SSH, t('watch.no_ssh', host=self.host))

        try:
            with Robot(self.host, self.user, self.password, timeout=8.0) as robot:
                identity = robot.identify()
        except Exception as exc:                        # noqa: BLE001
            # Reachable but refusing us: a wrong password, or something on
            # that address that is not an X2. Either way it is not "no robot".
            return Status(NO_SSH, str(exc))

        return Status(READY, t('watch.ready', identity=str(identity)), identity)
