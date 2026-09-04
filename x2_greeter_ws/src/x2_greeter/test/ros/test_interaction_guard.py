"""The guard that keeps the greeter from talking over the robot's own voice."""
import threading

import pytest

pytestmark = pytest.mark.ros

TTS_TOPIC = '/test/interaction/tts_status'
PLAY_TOPIC = '/test/interaction/play_state'
OWN_DOMAIN = 'x2_greeter'


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def rig(ros):
    """A guard, and publishers for the two topics it listens to."""
    from aimdk_msgs.msg import PlayStateChange, TtsStatus
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.qos import qos_profile_sensor_data

    from x2_greeter.ros.interaction_guard import InteractionGuard

    publisher = ros.create_node('interaction_publisher')
    tts_pub = publisher.create_publisher(TtsStatus, TTS_TOPIC, qos_profile_sensor_data)
    play_pub = publisher.create_publisher(PlayStateChange, PLAY_TOPIC,
                                          qos_profile_sensor_data)

    consumer = ros.create_node('interaction_consumer')
    guard = InteractionGuard(consumer, own_domain=OWN_DOMAIN,
                             tts_status_topic=TTS_TOPIC,
                             play_state_topic=PLAY_TOPIC,
                             busy_timeout_s=15.0,
                             callback_group=ReentrantCallbackGroup())

    executor = MultiThreadedExecutor()
    executor.add_node(publisher)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()

    def send_tts(status, domain=''):
        msg = TtsStatus()
        msg.tts_status.value = int(status)
        msg.domain = domain
        _publish_until_seen(tts_pub, msg, guard, consumer)

    def send_play(state, pkg_name=''):
        msg = PlayStateChange()
        msg.state.value = int(state)
        msg.pkg_name = pkg_name
        _publish_until_seen(play_pub, msg, guard, consumer)

    yield guard, send_tts, send_play
    executor.shutdown()
    guard.destroy()
    consumer.destroy_node()
    publisher.destroy_node()
    thread.join(timeout=5.0)


def _publish_until_seen(pub, msg, guard, node, attempts=60):
    """Publish repeatedly until the guard's state stops changing.

    Discovery between two nodes in the same process is not instant, and a
    single publish into an unmatched subscription is simply lost. Republishing
    an idempotent state message is the honest way to wait for the match.
    """
    import time
    before = guard.busy_reason()
    for _ in range(attempts):
        pub.publish(msg)
        time.sleep(0.05)
        if guard.busy_reason() != before:
            return
    # Unchanged may be correct -- an ignored message must not change anything.


# ------------------------------------------------------------- the basics

def test_a_quiet_robot_may_be_greeted(rig):
    guard, _, _ = rig
    assert guard.busy_reason() is None


def test_the_interaction_system_speaking_holds_greetings(rig):
    from aimdk_msgs.msg import TtsStatusType

    guard, send_tts, _ = rig
    send_tts(TtsStatusType.TTS_STATUS_TYPE_PLAYING, domain='interaction')

    reason = guard.busy_reason()
    assert reason is not None and 'speaking' in reason


def test_speech_merely_queued_also_holds_greetings(rig):
    # The robot is committed to saying it; greeting over the top of it a
    # moment later is the same interruption.
    from aimdk_msgs.msg import TtsStatusType

    guard, send_tts, _ = rig
    send_tts(TtsStatusType.TTS_STATUS_TYPE_INQUE, domain='interaction')

    assert guard.busy_reason() is not None


def test_the_end_of_speech_releases_the_hold(rig):
    from aimdk_msgs.msg import TtsStatusType

    guard, send_tts, _ = rig
    send_tts(TtsStatusType.TTS_STATUS_TYPE_PLAYING, domain='interaction')
    assert guard.busy_reason() is not None

    send_tts(TtsStatusType.TTS_STATUE_TYPE_END, domain='interaction')
    assert guard.busy_reason() is None


def test_audio_playing_from_another_package_holds_greetings(rig):
    from aimdk_msgs.msg import PlayStateType

    guard, _, send_play = rig
    send_play(PlayStateType.PLAYER_STATE_PLAYING, pkg_name='interaction')

    reason = guard.busy_reason()
    assert reason is not None and 'interaction' in reason


def test_audio_stopping_releases_the_hold(rig):
    from aimdk_msgs.msg import PlayStateType

    guard, _, send_play = rig
    send_play(PlayStateType.PLAYER_STATE_PLAYING, pkg_name='interaction')
    assert guard.busy_reason() is not None

    send_play(PlayStateType.PLAYER_STATE_STOPED, pkg_name='interaction')
    assert guard.busy_reason() is None


# ------------------------------------------- not hearing ourselves speak

def test_the_greeters_own_tts_does_not_hold_its_own_greetings(rig):
    """Without this the node hears itself start speaking, marks itself busy,
    and refuses every greeting until the timeout expires."""
    from aimdk_msgs.msg import TtsStatusType

    guard, send_tts, _ = rig
    send_tts(TtsStatusType.TTS_STATUS_TYPE_PLAYING, domain=OWN_DOMAIN)

    assert guard.busy_reason() is None


def test_the_greeters_own_audio_playback_does_not_hold_it_either(rig):
    from aimdk_msgs.msg import PlayStateType

    guard, _, send_play = rig
    send_play(PlayStateType.PLAYER_STATE_PLAYING, pkg_name=OWN_DOMAIN)

    assert guard.busy_reason() is None


def test_our_own_end_of_speech_does_not_clear_somebody_elses_hold(rig):
    # Our messages are ignored in both directions. Treating our own END as a
    # release would let the greeter clear a hold the interaction system had
    # just taken.
    from aimdk_msgs.msg import TtsStatusType

    guard, send_tts, _ = rig
    send_tts(TtsStatusType.TTS_STATUS_TYPE_PLAYING, domain='interaction')
    assert guard.busy_reason() is not None

    send_tts(TtsStatusType.TTS_STATUE_TYPE_END, domain=OWN_DOMAIN)
    assert guard.busy_reason() is not None


# --------------------------------------------------------- the expiry rule

def test_a_hold_expires_rather_than_muting_the_greeter_forever(ros):
    """Both topics are event-driven. A missed end-of-speech must not leave the
    greeter silent for the rest of the day."""
    from aimdk_msgs.msg import TtsStatus, TtsStatusType
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.interaction_guard import InteractionGuard

    node = ros.create_node('expiry_consumer')
    guard = InteractionGuard(node, own_domain=OWN_DOMAIN,
                             tts_status_topic=TTS_TOPIC,
                             play_state_topic=PLAY_TOPIC,
                             busy_timeout_s=0.2,
                             callback_group=ReentrantCallbackGroup())
    try:
        msg = TtsStatus()
        msg.tts_status.value = TtsStatusType.TTS_STATUS_TYPE_PLAYING
        msg.domain = 'interaction'
        guard._on_tts(msg)                      # delivered, no discovery race
        assert guard.busy_reason() is not None

        import time
        time.sleep(0.35)
        assert guard.busy_reason() is None
    finally:
        guard.destroy()
        node.destroy_node()


def test_our_own_greeting_holds_the_guard_while_its_audio_plays(rig):
    """Measured on hardware: PlayStateChange.pkg_name names the *player*, so
    everything spoken through PlayTts reports 'tts' -- ours included. Nothing
    in the message identifies the caller, so this cannot be filtered out, and
    it is left as it is: while one greeting is still being delivered the robot
    should not start another at whoever has just walked up.
    """
    from aimdk_msgs.msg import PlayStateType

    guard, _, send_play = rig
    send_play(PlayStateType.PLAYER_STATE_PLAYING, pkg_name='tts')

    reason = guard.busy_reason()
    assert reason is not None and 'tts' in reason
