"""Speech to text, locally on PC2, with the model injected.

No test here loads a real Whisper model: the adapter takes an
already-constructed model object, so the tests can stub it. A suite that
needs a 500 MB download is a suite people stop running.
"""
import pytest

from x2_greeter.cognition.transcriber import (
    SAMPLE_RATE_HZ, FasterWhisperTranscriber, TranscriptionUnavailable,
    Utterance)
from x2_greeter.sim.fakes import ScriptedTranscriber


class _Segment:
    def __init__(self, text, avg_logprob=-0.1, no_speech_prob=0.05):
        self.text = text
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


class _Info:
    def __init__(self, language='en', language_probability=0.98):
        self.language = language
        self.language_probability = language_probability


class _StubModel:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, segments, info=None, raises=None):
        self._segments = segments
        self._info = info or _Info()
        self._raises = raises
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self._raises is not None:
            raise self._raises
        return iter(self._segments), self._info


def _pcm(samples=16000):
    # One second of silence is fine: the stub decides what comes back.
    return b'\x00\x00' * samples


def test_the_sample_rate_is_the_vendor_sample_rate():
    assert SAMPLE_RATE_HZ == 16000


def test_an_utterance_knows_when_it_is_empty():
    assert Utterance('', 'en', 0.9).is_empty is True
    assert Utterance('   ', 'en', 0.9).is_empty is True
    assert Utterance('hello', 'en', 0.9).is_empty is False


def test_transcribing_joins_the_segments_and_trims():
    model = _StubModel([_Segment(' Hello there.'), _Segment(' How are you?')])
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.text == 'Hello there. How are you?'
    assert result.language == 'en'
    assert result.confidence == pytest.approx(0.98)


def test_a_chinese_result_keeps_its_language():
    model = _StubModel([_Segment('你好')], _Info('zh', 0.91))
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.text == '你好'
    assert result.language == 'zh'


def test_an_unsupported_language_is_reported_as_none_not_as_itself():
    # Malay is deferred. The transcriber may well detect it; the rest of the
    # system must see "not a language we speak", not a tag it will later try
    # to switch to.
    model = _StubModel([_Segment('apa khabar')], _Info('ms', 0.88))
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.language is None
    assert result.text == 'apa khabar'


def test_a_regional_tag_is_normalised():
    model = _StubModel([_Segment('hi')], _Info('en-US', 0.9))
    assert FasterWhisperTranscriber(model=model).transcribe(
        _pcm(), SAMPLE_RATE_HZ).language == 'en'


def test_no_segments_produces_an_empty_utterance_rather_than_an_error():
    # Silence, a cough, or the tail of the robot's own voice. Not a failure.
    result = FasterWhisperTranscriber(model=_StubModel([])).transcribe(
        _pcm(), SAMPLE_RATE_HZ)
    assert result.is_empty


def test_empty_audio_never_reaches_the_model():
    model = _StubModel([_Segment('should not happen')])
    result = FasterWhisperTranscriber(model=model).transcribe(b'', SAMPLE_RATE_HZ)
    assert result.is_empty
    assert model.calls == []


def test_the_pcm_is_converted_to_normalised_float_mono():
    import numpy as np

    model = _StubModel([_Segment('x')])
    # +32767 and -32768 as little-endian int16.
    FasterWhisperTranscriber(model=model).transcribe(
        b'\xff\x7f\x00\x80', SAMPLE_RATE_HZ)
    audio, _ = model.calls[0]
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.shape == (2,)
    assert audio[0] == pytest.approx(1.0, abs=1e-4)
    assert audio[1] == pytest.approx(-1.0, abs=1e-4)


def test_an_odd_length_buffer_drops_the_trailing_byte_instead_of_crashing():
    model = _StubModel([_Segment('x')])
    FasterWhisperTranscriber(model=model).transcribe(b'\x01\x02\x03', SAMPLE_RATE_HZ)
    audio, _ = model.calls[0]
    assert audio.shape == (1,)


def test_a_sample_rate_the_model_was_not_built_for_is_refused():
    model = _StubModel([_Segment('x')])
    with pytest.raises(TranscriptionUnavailable):
        FasterWhisperTranscriber(model=model).transcribe(_pcm(), 44100)
    assert model.calls == []


def test_a_model_failure_becomes_transcription_unavailable():
    # Every failure has one name upstream, so conversation.py has one branch.
    model = _StubModel([], raises=RuntimeError('CUDA out of memory'))
    with pytest.raises(TranscriptionUnavailable) as exc:
        FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert 'CUDA out of memory' in str(exc.value)


def test_no_audio_bytes_appear_in_any_log_line(caplog):
    import logging

    model = _StubModel([], raises=RuntimeError('boom'))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TranscriptionUnavailable):
        FasterWhisperTranscriber(model=model, logger=logging.getLogger('x2')
                                 ).transcribe(b'\xde\xad\xbe\xef' * 100,
                                              SAMPLE_RATE_HZ)
    joined = ' '.join(record.getMessage() for record in caplog.records)
    assert 'dead' not in joined.lower()
    assert '\\xde' not in joined


def test_the_scripted_fake_pops_one_utterance_per_call():
    fake = ScriptedTranscriber([Utterance('one', 'en', 0.9),
                                Utterance('two', 'en', 0.9)])
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).text == 'one'
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).text == 'two'
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).is_empty


def test_the_scripted_fake_can_be_asked_to_fail_when_it_runs_out():
    fake = ScriptedTranscriber([], raise_when_exhausted=True)
    with pytest.raises(TranscriptionUnavailable):
        fake.transcribe(b'', SAMPLE_RATE_HZ)
