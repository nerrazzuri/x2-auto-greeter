"""Is the robot reachable, and if not, what should the customer do about it.

This does not configure anything. Changing a network adapter needs privileges
the deployer should not be asking a customer for, and the two platforms have
nothing in common to do it with -- nmcli on Linux, netsh on Windows, neither
present on the other. What both platforms do share is the question worth
answering: is there a route to 10.0.1.41 from this machine, and if not, which
of the two ordinary reasons is it?

The two reasons are the cable and the address. Telling them apart matters: one
is solved by pushing a plug in, the other by typing an address into a settings
panel, and sending someone to the wrong one wastes the visit.
"""
from __future__ import annotations

import ipaddress
import platform
import socket
from dataclasses import dataclass
from typing import Optional

from .i18n import t

SSH_PORT = 22


@dataclass(frozen=True)
class Reachability:
    reachable: bool
    local_address: Optional[str]
    detail: str

    def __bool__(self) -> bool:
        return self.reachable


def route_address(host: str) -> Optional[str]:
    """The local address this machine would use to reach `host`, or None.

    Opens a UDP socket and asks the kernel which source address it would pick.
    Nothing is sent -- UDP connect only fixes the peer -- so this answers in
    microseconds and works the same on Windows, Linux and macOS.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def port_open(host: str, port: int = SSH_PORT, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def same_subnet(local: str, host: str, prefix: int = 24) -> bool:
    try:
        network = ipaddress.ip_network(f'{host}/{prefix}', strict=False)
        return ipaddress.ip_address(local) in network
    except ValueError:
        return False


def fix_instructions(host: str) -> str:
    """What to do about it, in the words of the machine the customer is on."""
    suggested = _suggest_address(host)
    key = ('net.fix_windows' if platform.system() == 'Windows'
           else 'net.fix_linux')
    return t(key, address=suggested)


def _suggest_address(host: str) -> str:
    """An address on the robot's subnet that is not the robot's own."""
    try:
        parts = str(ipaddress.ip_address(host)).split('.')
        return '.'.join(parts[:3] + ['49'])
    except ValueError:
        return '10.0.1.49'


def check(host: str, timeout: float = 3.0) -> Reachability:
    """Reachable, or the most likely reason it is not."""
    local = route_address(host)

    if port_open(host, timeout=timeout):
        return Reachability(True, local, t('net.connected', host=host))

    if local is None or not same_subnet(local, host):
        where = t('net.here', address=local) if local else ''
        return Reachability(
            False, local,
            t('net.wrong_subnet', where=where, fix=fix_instructions(host)))

    return Reachability(
        False, local, t('net.silent', address=local, host=host))
