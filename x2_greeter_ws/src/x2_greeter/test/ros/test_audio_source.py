"""The vendor audio source: VAD segments in, one utterance out.

Marked `ros` -- it needs aimdk_msgs and runs in the container. aimdk_msgs
and x2_greeter.ros.* are imported inside each fixture/test rather than at
module scope, matching the other ros/ test files: a module-level import
would break collection (and so the whole host suite, since `-m 'not ros'`
still has to import a module before it can see its marker) on a machine
without rclpy and aimdk_msgs installed.
"""
import pytest

pytestmark = pytest.mark.ros


class _FakeSub:
    def __init__(self):
        self.destroyed = False


class _FakeNode:
    """Just enough node to hold a subscription and a clock."""

    def __init__(self):
        self.callback = None
        self.subscription = None
        self.logs = []

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.callback = callback
        self.topic = topic
        self.msg_type = msg_type
        self.subscription = _FakeSub()
        return self.subscription

    def destroy_subscription(self, sub):
        sub.destroyed = True

    def get_logger(self):
        node = self

        class _Logger:
            def info(self, msg): node.logs.append(msg)
            def warn(self, msg): node.logs.append(msg)
            def warning(self, msg): node.logs.append(msg)
            def error(self, msg): node.logs.append(msg)
            def debug(self, msg): node.logs.append(msg)
        return _Logger()


def _chunk(state, data=b'', stream_id=1):
    from aimdk_msgs.msg import ProcessedAudioOutput

    msg = ProcessedAudioOutput()
    msg.stream_id = stream_id
    msg.audio_vad_state.value = state
    msg.audio_data = list(data)
    return msg


@pytest.fixture
def wired():
    from x2_greeter.ros.audio_source import VendorAudioSource

    node = _FakeNode()
    got = []
    source = VendorAudioSource(node=node, on_utterance=lambda pcm, at_s:
                               got.append((pcm, at_s)))
    source.start()
    return node, source, got


def test_it_subscribes_to_the_processed_audio_topic(wired):
    from aimdk_msgs.msg import ProcessedAudioOutput

    node, source, _ = wired
    assert node.topic == '/agent/process_audio_output'
    assert node.msg_type is ProcessedAudioOutput


def test_a_complete_utterance_is_delivered_once_as_joined_pcm(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'ab'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_PROCESSING, b'cd'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b'ef'))
    assert [pcm for pcm, _ in got] == [b'abcdef']
    assert source.utterances_seen == 1


def test_the_buffer_is_empty_again_after_delivery(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, _ = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'ab'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b'cd'))
    assert source.bytes_buffered == 0, (
        'audio lives for one utterance and is then gone')


def test_a_second_utterance_does_not_carry_the_first_one_forward(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    for payload in (b'one', b'two'):
        node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, payload))
        node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert [pcm for pcm, _ in got] == [b'one', b'two']


def test_audio_arriving_before_any_begin_is_dropped(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_PROCESSING, b'half a sentence'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert got == [], 'we joined mid-utterance; half a sentence is worse than none'


def test_an_empty_utterance_is_not_delivered(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b''))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert got == []


def test_a_new_begin_abandons_an_unfinished_utterance(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'abandoned'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'kept'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert [pcm for pcm, _ in got] == [b'kept']


def test_a_second_stream_id_replaces_the_first(wired):
    # Two overlapping streams means the vendor moved on; follow it rather
    # than interleaving two people's speech into one buffer.
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'aaa', stream_id=1))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'bbb', stream_id=2))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b'', stream_id=2))
    assert [pcm for pcm, _ in got] == [b'bbb']


def test_an_over_long_utterance_is_abandoned_rather_than_grown_forever():
    from aimdk_msgs.msg import AudioVadStateType
    from x2_greeter.ros.audio_source import VendorAudioSource

    node = _FakeNode()
    got = []
    # 16 kHz * 2 bytes * 0.5 s = 16000 bytes
    source = VendorAudioSource(node=node, on_utterance=lambda p, t: got.append(p),
                               max_utterance_s=0.5)
    source.start()
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'\x00' * 20000))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert got == []
    assert source.bytes_buffered == 0


def test_stop_drops_whatever_is_buffered(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'partial'))
    source.stop()
    assert source.bytes_buffered == 0
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert got == [], 'a stopped source delivers nothing'


def test_start_after_stop_works_again(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, got = wired
    source.stop()
    source.start()
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'hi'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert [pcm for pcm, _ in got] == [b'hi']


def test_destroy_releases_the_subscription(wired):
    node, source, _ = wired
    sub = node.subscription
    source.destroy()
    assert sub.destroyed is True


def test_no_audio_bytes_reach_the_log(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, _ = wired
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'\xde\xad\xbe\xef' * 100))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    joined = ' '.join(node.logs)
    assert 'dead' not in joined.lower()
    assert '\xde' not in joined
    assert 'deadbeef' not in joined.lower()


def test_a_callback_that_raises_does_not_kill_the_subscription(wired):
    from aimdk_msgs.msg import AudioVadStateType

    node, source, _ = wired

    def _boom(pcm, at_s):
        raise RuntimeError('downstream exploded')

    source._on_utterance = _boom
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'hi'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    # Still alive, still segmenting.
    source._on_utterance = lambda pcm, at_s: None
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b'again'))
    node.callback(_chunk(AudioVadStateType.AUDIO_VAD_STATE_END, b''))
    assert source.utterances_seen == 2
