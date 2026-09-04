import threading

from x2_greeter.ros.service_call import call_with_retry, wait_for_future


class FakeFuture:
    """Mimics an rclpy Future: done callbacks fire immediately if already done."""

    def __init__(self, result=None, complete_after_s=None):
        self._result = result
        self._done = complete_after_s is None
        self.cancelled = False
        self._callbacks = []
        if complete_after_s is not None:
            timer = threading.Timer(complete_after_s, self._complete)
            timer.daemon = True
            timer.start()

    def _complete(self):
        self._done = True
        for callback in list(self._callbacks):
            callback(self)

    def add_done_callback(self, callback):
        if self._done:
            callback(self)
        else:
            self._callbacks.append(callback)

    def cancel(self):
        self.cancelled = True

    def result(self):
        return self._result


class FakeClient:
    def __init__(self, futures):
        self._futures = list(futures)
        self.requests = []

    def call_async(self, request):
        self.requests.append(request)
        return self._futures.pop(0)


def test_an_immediate_response_is_returned_on_the_first_attempt():
    client = FakeClient([FakeFuture(result='ok')])
    assert call_with_retry(client, 'req') == 'ok'
    assert len(client.requests) == 1


def test_a_never_completing_future_is_retried_the_configured_number_of_times():
    futures = [FakeFuture(complete_after_s=10.0) for _ in range(3)]
    client = FakeClient(futures)
    assert call_with_retry(client, 'req', attempts=3, timeout_s=0.01) is None
    assert len(client.requests) == 3


def test_stuck_attempts_are_cancelled():
    futures = [FakeFuture(complete_after_s=10.0) for _ in range(2)]
    client = FakeClient(futures)
    call_with_retry(client, 'req', attempts=2, timeout_s=0.01)
    assert all(f.cancelled for f in futures)


def test_a_late_success_is_picked_up_by_a_later_attempt():
    client = FakeClient([FakeFuture(complete_after_s=10.0), FakeFuture(result='ok')])
    assert call_with_retry(client, 'req', attempts=8, timeout_s=0.01) == 'ok'
    assert len(client.requests) == 2


def test_the_header_is_refreshed_before_every_attempt():
    stamps = []
    client = FakeClient([FakeFuture(complete_after_s=10.0), FakeFuture(result='ok')])
    call_with_retry(client, 'req', before_attempt=lambda r: stamps.append(r),
                    attempts=8, timeout_s=0.01)
    assert len(stamps) == 2


def test_wait_for_future_reports_completion():
    assert wait_for_future(FakeFuture(result='ok'), 0.01) is True


def test_wait_for_future_reports_a_timeout():
    assert wait_for_future(FakeFuture(complete_after_s=10.0), 0.01) is False


# ------------------------------------- waiting for the service to be found

class _DiscoverableClient:
    """A client that is not discovered for the first `not_ready` waits."""

    def __init__(self, not_ready=0, answers=True, ever_ready=True):
        self.not_ready = not_ready
        self.answers = answers
        self.ever_ready = ever_ready
        self.waits = 0
        self.calls = 0

    def wait_for_service(self, timeout_sec=None):
        self.waits += 1
        # An instant no-op wait: this fake is spun thousands of times inside a
        # short budget, so "not ready for the first N waits" cannot express
        # "never ready" -- N is reached in microseconds. ever_ready does.
        return self.ever_ready and self.waits > self.not_ready

    def call_async(self, request):
        self.calls += 1
        return _ImmediateFuture('answered') if self.answers else _NeverFuture()


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def add_done_callback(self, callback):
        callback(self)

    def result(self):
        return self._value

    def cancel(self):
        pass


class _NeverFuture:
    def add_done_callback(self, callback):
        pass

    def result(self):
        return None

    def cancel(self):
        pass


def test_it_waits_for_the_service_before_spending_its_attempts():
    """The fault this fixes: call_async on a service the client has not
    discovered yet returns a future that never completes, so all eight
    attempts are burnt against a service that is running and healthy.
    Measured on an X2: 0 of 8 from a cold client, 15 of 15 at a median of
    3 ms once the client had waited."""
    from x2_greeter.ros.service_call import call_with_retry

    client = _DiscoverableClient(not_ready=3)
    assert call_with_retry(client, object(), timeout_s=0.01) == 'answered'
    assert client.waits == 4, 'gave up waiting too early'
    assert client.calls == 1, 'called before the service was there'


def test_a_service_that_never_appears_reports_nothing_rather_than_calling():
    from x2_greeter.ros.service_call import call_with_retry

    client = _DiscoverableClient(ever_ready=False)
    assert call_with_retry(client, object(), attempts=3, timeout_s=0.01) is None
    assert client.calls == 0


def test_a_client_without_wait_for_service_is_treated_as_ready():
    # Every existing test double in this repo is one of these.
    from x2_greeter.ros.service_call import await_service

    assert await_service(object(), budget_s=0.0, step_s=0.01) is True
