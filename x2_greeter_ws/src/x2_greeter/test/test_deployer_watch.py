"""Watching for the robot instead of asking the customer to press a button.

The states are ordered by how far the customer has got, and the tests are
written the same way: no cable, cable but wrong address, right address but no
robot, robot answering. Each has a different fix, and telling them apart is
the entire value -- a customer sent to the network settings when the plug is
loose has been sent to the wrong place.

The throttle is here too. Polling every two seconds and logging each time
would bury the deployment's own output inside a minute.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import network, watch                        # noqa: E402
from deployer.core.robot import Identity                        # noqa: E402

ROBOT = '10.0.1.41'


@pytest.fixture
def watcher():
    return watch.Watcher(ROBOT)


# -- the four states ---------------------------------------------------------

def test_no_route_at_all_means_the_cable(watcher, monkeypatch):
    monkeypatch.setattr(network, 'route_address', lambda _h: None)
    status = watcher.poll()
    assert status.state == watch.NO_LINK
    assert '网线' in status.detail


def test_an_address_off_the_robots_subnet_says_which_address(watcher, monkeypatch):
    monkeypatch.setattr(network, 'route_address', lambda _h: '192.168.100.147')
    status = watcher.poll()
    assert status.state == watch.WRONG_SUBNET
    assert '192.168.100.147' in status.detail
    assert '10.0.1.49' in status.detail, '要给一个能照着填的地址'


def test_the_right_subnet_with_nothing_answering_is_not_a_network_problem(
        watcher, monkeypatch):
    """Same failure to connect as the case above, opposite fix. This one is
    the plug or the power switch, and saying "wrong network" would send the
    customer to change an address that is already correct."""
    monkeypatch.setattr(network, 'route_address', lambda _h: '10.0.1.49')
    monkeypatch.setattr(network, 'port_open', lambda *a, **k: False)
    status = watcher.poll()
    assert status.state == watch.NO_SSH
    assert '控制面板' not in status.detail and '设置 → 网络' not in status.detail


def test_a_robot_that_answers_reports_which_unit_it_is(watcher, monkeypatch):
    """Every X2 shares its SSH host keys, so the serial is the only thing that
    says which robot is on the other end of the cable."""
    monkeypatch.setattr(network, 'route_address', lambda _h: '10.0.1.49')
    monkeypatch.setattr(network, 'port_open', lambda *a, **k: True)

    class FakeRobot:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def identify(self):
            return Identity(serial='1423225023973', mac='e8:97:44:8f:19:df',
                            hostname='agi')

    monkeypatch.setattr(watch, 'Robot', FakeRobot)
    status = watcher.poll()
    assert status.ready
    assert status.identity.serial == '1423225023973'
    assert '1423225023973' in status.detail


def test_a_refused_login_is_reported_rather_than_raised(watcher, monkeypatch):
    """Something is on that address and will not let us in -- a wrong
    password, or a machine that is not an X2. The window must keep polling,
    not fall over."""
    monkeypatch.setattr(network, 'route_address', lambda _h: '10.0.1.49')
    monkeypatch.setattr(network, 'port_open', lambda *a, **k: True)

    class Refusing:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            raise RuntimeError('密码不对')

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(watch, 'Robot', Refusing)
    status = watcher.poll()
    assert status.state == watch.NO_SSH
    assert '密码' in status.detail


def test_ssh_is_not_attempted_until_the_network_is_right(watcher, monkeypatch):
    """An SSH attempt to an unroutable address blocks for its whole timeout,
    and this runs every two seconds."""
    monkeypatch.setattr(network, 'route_address', lambda _h: '192.168.1.5')
    tried = []
    monkeypatch.setattr(watch, 'Robot',
                        lambda *a, **k: tried.append(True))
    watcher.poll()
    assert tried == []


# -- not spamming the log ----------------------------------------------------

def test_a_change_is_always_worth_a_line():
    throttle = watch.Throttle(repeat_s=10)
    assert throttle.should_log(watch.Status(watch.NO_LINK, ''), now=0)
    assert throttle.should_log(watch.Status(watch.WRONG_SUBNET, ''), now=1)
    assert throttle.should_log(watch.Status(watch.READY, ''), now=2)


def test_the_same_news_repeats_slowly():
    """A cable left unplugged for ten minutes costs six lines, not three
    hundred."""
    throttle = watch.Throttle(repeat_s=10)
    same = watch.Status(watch.NO_LINK, '')
    assert throttle.should_log(same, now=0)
    assert not throttle.should_log(same, now=2)
    assert not throttle.should_log(same, now=8)
    assert throttle.should_log(same, now=10)
    assert not throttle.should_log(same, now=12)


def test_going_back_to_a_previous_state_is_news_again():
    throttle = watch.Throttle(repeat_s=10)
    throttle.should_log(watch.Status(watch.READY, ''), now=0)
    assert throttle.should_log(watch.Status(watch.NO_LINK, ''), now=1)
    assert throttle.should_log(watch.Status(watch.READY, ''), now=2)


def test_polling_and_repeat_intervals_are_what_was_asked_for():
    assert watch.POLL_S == 2.0
    assert watch.REPEAT_S == 10.0
