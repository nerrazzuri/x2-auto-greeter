"""Saying the greeting out loud.

The interface docs never state whether PlayTts synthesises onboard or in the
cloud, so this hedges (spec section 10): speak through TTS, and if TTS reports
failure, fall back permanently to pre-recorded audio files for the rest of the
session. If onboard TTS turns out to work offline, the fallback is simply
never reached.
"""
from __future__ import annotations

import os
import random
from typing import Optional

from aimdk_msgs.srv import PlayAudioFile, PlayTts

from x2_greeter.ros.service_call import call_with_retry

VALID_TIERS = ('auto', 'tts', 'audio_file')

TTS_SERVICE = '/aimdk_5Fmsgs/srv/PlayTts'
AUDIO_SERVICE = '/aimdk_5Fmsgs/srv/PlayAudioFile'

# Documented in the interface reference: 16 kHz, 16-bit, mono, WAV or raw PCM.
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_RATE = 16000
AUDIO_SAMPLE_FORMAT = 'S16_LE'
AUDIO_CODING_FORMAT = 'wave'


class SpeechDispatcher:
    def __init__(self, node, tier: str = 'auto', domain: str = 'x2_greeter',
                 priority_level: int = 6, audio_dir: str = '/var/tmp/x2_greeter_audio',
                 audio_file_count: int = 6, rng: Optional[random.Random] = None,
                 callback_group=None) -> None:
        if tier not in VALID_TIERS:
            raise ValueError(f'speech.tier must be one of {VALID_TIERS}, got {tier!r}')

        self._node = node
        self._tier = tier
        self._domain = domain
        self._priority_level = int(priority_level)
        self._audio_dir = audio_dir
        self._audio_file_count = max(1, int(audio_file_count))
        self._rng = rng if rng is not None else random.Random()
        self._using_tts = tier in ('auto', 'tts')
        self._demoted = False
        self._audio_error_logged = False

        self._tts_client = node.create_client(PlayTts, TTS_SERVICE,
                                              callback_group=callback_group)
        self._audio_client = node.create_client(PlayAudioFile, AUDIO_SERVICE,
                                                callback_group=callback_group)

    @property
    def using_tts(self) -> bool:
        return self._using_tts

    @property
    def demoted(self) -> bool:
        return self._demoted

    def speak(self, text: str) -> bool:
        """Say `text`, or play a recording if TTS is unavailable. True if it spoke."""
        text = (text or '').strip()
        if not text:
            self._node.get_logger().warning('refusing to speak an empty greeting')
            return False

        if not self._using_tts:
            return self._play_audio_file()

        if self._play_tts(text):
            return True

        if self._tier != 'auto':
            return False

        # One-way demotion: TTS has failed, so stop asking it for the rest of
        # this session and use the recordings instead.
        self._using_tts = False
        self._demoted = True
        self._node.get_logger().warning(
            f'PlayTts reported failure; demoting to pre-recorded audio files in '
            f'{self._audio_dir} for the rest of this session')
        return self._play_audio_file()

    def _play_tts(self, text: str) -> bool:
        request = PlayTts.Request()
        request.tts_req.text = text
        request.tts_req.domain = self._domain
        request.tts_req.trace_id = 'x2_greeter'
        request.tts_req.is_interrupted = True       # interrupt same-priority speech
        request.tts_req.priority_weight = 0
        request.tts_req.priority_level.value = self._priority_level

        def stamp(req):
            req.header.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._tts_client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._node.get_logger().error('PlayTts did not respond')
            return False
        if not response.tts_resp.is_success:
            self._node.get_logger().error(
                f'PlayTts failed: {response.tts_resp.error_message}')
            return False
        return True

    def _play_audio_file(self) -> bool:
        index = self._rng.randrange(self._audio_file_count)
        file_name = f'greeting_{index:02d}.wav'

        request = PlayAudioFile.Request()
        request.file.pkg_name = 'x2_greeter'
        request.file.file_name = file_name
        request.file.file_path = self._audio_dir
        request.file.priority = self._priority_level
        request.file.priority_weight = 0
        request.file.info.channels = AUDIO_CHANNELS
        request.file.info.sample_rate = AUDIO_SAMPLE_RATE
        request.file.info.sample_format = AUDIO_SAMPLE_FORMAT
        request.file.info.coding_format = AUDIO_CODING_FORMAT

        def stamp(req):
            req.request.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._audio_client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._log_audio_error(f'PlayAudioFile did not respond for {file_name}')
            return False

        # The vendor .srv spells the response field 'reponse' (sic). Read it
        # defensively so this keeps working if they fix the typo.
        common = getattr(response, 'reponse', None)
        if common is None:
            common = getattr(response, 'response', None)
        if common is None or common.header.code != 0:
            self._log_audio_error(
                f'PlayAudioFile rejected {os.path.join(self._audio_dir, file_name)}; '
                'is the file deployed to PC3 and world-readable?')
            return False
        return True

    def _log_audio_error(self, message: str) -> None:
        """Audio assets live on PC3, so this is a deployment problem, not a bug.

        Logged once: a missing recording would otherwise shout on every greeting.
        """
        if self._audio_error_logged:
            self._node.get_logger().debug(message)
            return
        self._node.get_logger().error(message)
        self._audio_error_logged = True
