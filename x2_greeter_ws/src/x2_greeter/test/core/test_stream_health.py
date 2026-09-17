"""The camera watchdog: judged on usable RGB-D pairs, not on RGB alone."""
import pytest

from x2_greeter.core.stream_health import StreamHealth

STALE_S = 5.0


@pytest.fixture
def health():
    return StreamHealth(stale_s=STALE_S, started_at=0.0)


def test_quiet_while_pairs_keep_arriving(health):
    for t in range(0, 20):
        health.rgb(float(t))
        health.depth(float(t))
        health.pair(float(t))
        assert health.check(float(t)) is None


def test_warns_when_rgb_flows_but_depth_has_died(health):
    """The case the old watchdog missed: RGB alive kept it quiet forever."""
    health.rgb(0.0)
    health.depth(0.0)
    health.pair(0.0)
    for t in range(1, 7):
        health.rgb(float(t))
    warning = health.check(6.0)
    assert warning is not None
    assert 'depth last arrived 6.0s ago' in warning
    assert 'colour last arrived 0.0s ago' in warning


def test_warns_when_no_frame_ever_arrived(health):
    """The old watchdog stayed silent until the first RGB frame, i.e. forever."""
    warning = health.check(STALE_S + 1.0)
    assert warning is not None
    assert 'colour never arrived' in warning
    assert 'depth never arrived' in warning


def test_warns_when_both_streams_arrive_but_never_pair(health):
    for t in range(0, 7):
        health.rgb(float(t))
        health.depth(float(t))
    warning = health.check(6.0)
    assert warning is not None
    assert 'not pairing' in warning


def test_warns_once_per_outage(health):
    assert health.check(6.0) is not None
    assert health.check(7.0) is None
    assert health.check(60.0) is None


def test_a_pair_after_an_outage_reports_recovery_and_rearms(health):
    assert health.check(6.0) is not None
    assert health.pair(7.0) is True
    assert health.pair(7.1) is False
    assert health.check(12.2) is not None


def test_counts_pairs(health):
    health.pair(1.0)
    health.pair(2.0)
    assert health.pairs == 2
