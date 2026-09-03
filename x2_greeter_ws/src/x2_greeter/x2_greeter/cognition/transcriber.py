"""Speech to text, on PC2, with no network involved.

faster-whisper `small` on the Jetson's GPU. The model is injected rather than
constructed here so that no test ever has to download or load one -- a suite
that needs half a gigabyte of weights is a suite that quietly stops being
run.

Privacy rule, same as the camera: the PCM lives in memory for one utterance
and is dropped. Nothing is written to disk, and no audio byte ever reaches a
log line at any level -- which is why the failure path logs the exception's
message and never the buffer.
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Protocol, Sequence

import numpy as np

from x2_greeter.core.language import normalise_language

SAMPLE_RATE_HZ = 16000     # the vendor publishes 16 kHz, 16-bit, mono, S16LE
_INT16_FULL_SCALE = 32768.0


class TranscriptionUnavailable(Exception):
    """The transcriber could not produce a result. One name for every cause."""


class Utterance(NamedTuple):
    text: str
    language: Optional[str]
    confidence: float

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class Transcriber(Protocol):
    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        ...


def _to_float_mono(pcm: bytes) -> np.ndarray:
    """S16LE bytes -> float32 in [-1, 1). An odd trailing byte is dropped."""
    usable = len(pcm) - (len(pcm) % 2)
    samples = np.frombuffer(pcm[:usable], dtype='<i2')
    return (samples.astype(np.float32) / _INT16_FULL_SCALE)


class FasterWhisperTranscriber:
    name = 'faster_whisper'

    def __init__(self, model_size: str = 'small', device: str = 'auto',
                 compute_type: str = 'int8', model=None, logger=None) -> None:
        self._logger = logger
        if model is None:
            # Imported lazily so importing this module -- which the layering
            # test does for every cognition module -- never pulls in the
            # runtime or its weights.
            from faster_whisper import WhisperModel
            model = WhisperModel(model_size, device=device,
                                 compute_type=compute_type)
        self._model = model

    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        if int(sample_rate) != SAMPLE_RATE_HZ:
            raise TranscriptionUnavailable(
                f'expected {SAMPLE_RATE_HZ} Hz audio, got {sample_rate} Hz')
        audio = _to_float_mono(pcm)
        if audio.size == 0:
            return Utterance(text='', language=None, confidence=0.0)

        try:
            segments, info = self._model.transcribe(
                audio, beam_size=1, vad_filter=False,
                condition_on_previous_text=False)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:                  # noqa: BLE001 - one name upstream
            if self._logger is not None:
                # The message only. Never the buffer, never its length in a
                # form that could be mistaken for content.
                self._logger.warning(f'transcription failed: {exc}')
            raise TranscriptionUnavailable(str(exc)) from exc

        return Utterance(
            text=text,
            language=normalise_language(getattr(info, 'language', None)),
            confidence=float(getattr(info, 'language_probability', 0.0) or 0.0),
        )
