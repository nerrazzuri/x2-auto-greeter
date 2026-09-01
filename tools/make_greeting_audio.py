#!/usr/bin/env python3
"""Generate the tier-3 greeting recordings.

The interface docs are strict: 16 kHz, 16-bit, mono, WAV or raw PCM. MP3 is
rejected. Files are named greeting_NN.wav by phrase index, which is how
SpeechDispatcher finds them, so the order in config/phrases.yaml is
load-bearing.

Uses pyttsx3 for real speech when it is installed; otherwise it writes a
labelled placeholder tone so the deployment pipeline can be exercised before
real recordings exist. Replace the placeholders with proper audio before the
robot meets anybody.

Usage:
    python tools/make_greeting_audio.py --dest assets/audio
    python tools/make_greeting_audio.py --dest assets/audio --phrases path/to/phrases.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
import wave

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2       # 16-bit


def wav_filename(index: int) -> str:
    """The name SpeechDispatcher will ask PlayAudioFile for."""
    return f'greeting_{index:02d}.wav'


def write_wav(path: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write mono 16-bit PCM. `samples` is float in -1..1, or already int16."""
    if samples.dtype != np.int16:
        clipped = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
        samples = (clipped * 32767.0).astype(np.int16)
    with wave.open(path, 'wb') as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH_BYTES)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def synthesise(text: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Speech for `text` if a TTS engine is available, otherwise a placeholder tone.

    The placeholder's length tracks the phrase length, so a wrong-file mix-up
    is audible rather than silent.
    """
    engine_samples = _try_pyttsx3(text, sample_rate)
    if engine_samples is not None:
        return engine_samples

    duration_s = max(0.6, min(4.0, len(text) * 0.06))
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
    envelope = np.minimum(1.0, np.minimum(t * 20.0, (duration_s - t) * 20.0))
    return tone * envelope


def _try_pyttsx3(text: str, sample_rate: int):
    """Render with pyttsx3 into a temporary WAV, then resample to 16 kHz mono."""
    try:
        import tempfile

        import pyttsx3
    except ImportError:
        print('pyttsx3 not installed; writing placeholder tones', file=sys.stderr)
        return None

    tmp_path = None
    try:
        handle, tmp_path = tempfile.mkstemp(suffix='.wav')
        os.close(handle)
        engine = pyttsx3.init()
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        with wave.open(tmp_path, 'rb') as source:
            frames = source.readframes(source.getnframes())
            channels = source.getnchannels()
            source_rate = source.getframerate()
            width = source.getsampwidth()
        if width != 2 or not frames:
            return None
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32767.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if source_rate != sample_rate:
            target_length = int(round(len(data) * sample_rate / float(source_rate)))
            data = np.interp(np.linspace(0.0, len(data) - 1, target_length),
                             np.arange(len(data)), data)
        return data
    except Exception as exc:                           # noqa: BLE001 - fall back to a tone
        print(f'pyttsx3 unavailable ({exc}); writing placeholder tones', file=sys.stderr)
        return None
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_phrase_list(path: str):
    if path:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'x2_greeter_ws', 'src', 'x2_greeter'))
        from x2_greeter.cognition.canned import load_phrases
        return load_phrases(path)

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'x2_greeter_ws', 'src', 'x2_greeter'))
    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    return DEFAULT_PHRASES


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default='assets/audio',
                        help='directory to write greeting_NN.wav into')
    parser.add_argument('--phrases', default='',
                        help='phrases.yaml to read (default: the built-in list)')
    args = parser.parse_args(argv)

    phrases = load_phrase_list(args.phrases)
    os.makedirs(args.dest, exist_ok=True)

    for index, phrase in enumerate(phrases):
        path = os.path.join(args.dest, wav_filename(index))
        write_wav(path, synthesise(phrase))
        print(f'{path}  <-  {phrase!r}')

    print(f'\nWrote {len(phrases)} files to {os.path.abspath(args.dest)}.')
    print('Set speech.audio_file_count to', len(phrases), 'in config/greeter.yaml,')
    print('then deploy with tools/deploy_audio.sh — the files must live on PC3.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
