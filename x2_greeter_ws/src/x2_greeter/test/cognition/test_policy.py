import logging
import random

import pytest

from x2_greeter.cognition.canned import CannedBackend
from x2_greeter.cognition.policy import GreetingPolicy
from x2_greeter.cognition.port import BackendUnavailable
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

CTX = SceneContext(distance_m=2.0, center_offset=0.0)
FRAME = JpegFrame(data=b'\xff\xd8\xffbytes')

CLOUD_VERDICT = Verdict(person_present=True, facing_robot=True, confidence=0.9,
                        greeting='Hello from the cloud!', gesture='wave',
                        reason='a person', source='claude')


class StubBackend:
    def __init__(self, name, verdict=None, raises=None):
        self.name = name
        self._verdict = verdict
        self._raises = raises
        self.calls = []

    def confirm_and_compose(self, frame, ctx):
        self.calls.append((frame, ctx))
        if self._raises is not None:
            raise self._raises
        return self._verdict


def canned():
    return CannedBackend(rng=random.Random(0))


def test_a_working_primary_is_used():
    primary = StubBackend('claude', CLOUD_VERDICT)
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.greeting == 'Hello from the cloud!'
    assert verdict.source == 'claude'


def test_the_fallback_is_not_consulted_when_the_primary_works():
    fallback = StubBackend('canned', CLOUD_VERDICT)
    GreetingPolicy(StubBackend('claude', CLOUD_VERDICT), fallback).compose(FRAME, CTX)
    assert fallback.calls == []


def test_a_backend_unavailable_falls_back():
    primary = StubBackend('claude', raises=BackendUnavailable('timed out after 2.5s'))
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.source == 'canned'
    assert verdict.person_present is True


def test_an_unexpected_exception_also_falls_back():
    primary = StubBackend('claude', raises=RuntimeError('surprise'))
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.source == 'canned'


def test_the_fallback_is_not_shown_the_frame():
    fallback = StubBackend('canned', CLOUD_VERDICT)
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    GreetingPolicy(primary, fallback).compose(FRAME, CTX)
    assert fallback.calls == [(None, CTX)]


def test_a_negative_cloud_verdict_is_respected_not_overridden():
    # If the cloud says "that is a poster", we must not fall back to a canned
    # greeting — the fallback exists for failures, not for disagreements.
    negative = Verdict(person_present=False, facing_robot=False, confidence=0.95,
                       greeting='', gesture=None, reason='a poster', source='claude')
    fallback = StubBackend('canned', CLOUD_VERDICT)
    verdict = GreetingPolicy(StubBackend('claude', negative), fallback).compose(FRAME, CTX)
    assert verdict.person_present is False
    assert fallback.calls == []


def test_fallbacks_are_counted():
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    policy = GreetingPolicy(primary, canned())
    for _ in range(3):
        policy.compose(FRAME, CTX)
    assert policy.fallback_count == 3


def test_the_failure_reason_is_logged_as_a_warning(caplog):
    primary = StubBackend('claude', raises=BackendUnavailable('rate limited'))
    with caplog.at_level(logging.WARNING):
        GreetingPolicy(primary, canned(), logger=logging.getLogger('t')).compose(FRAME, CTX)
    assert 'rate limited' in caplog.text


def test_image_bytes_are_never_logged(caplog):
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    with caplog.at_level(logging.DEBUG):
        GreetingPolicy(primary, canned(), logger=logging.getLogger('t')).compose(FRAME, CTX)
    assert 'bytes' not in caplog.text.replace('image bytes', '')
    assert '\\xff\\xd8' not in caplog.text


def test_a_fallback_that_also_fails_propagates():
    # There is no third tier; if the canned backend is broken, that is a bug we
    # want to see, not swallow.
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    fallback = StubBackend('canned', raises=BackendUnavailable('also down'))
    with pytest.raises(BackendUnavailable, match='also down'):
        GreetingPolicy(primary, fallback).compose(FRAME, CTX)
