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
