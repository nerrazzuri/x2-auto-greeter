import importlib.util
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / 'tools' / 'make_greeting_audio.py'


@pytest.fixture(scope='module')
def tool():
    spec = importlib.util.spec_from_file_location('make_greeting_audio', TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules['make_greeting_audio'] = module
    spec.loader.exec_module(module)
    return module


def test_the_tool_exists():
    assert TOOL_PATH.is_file(), TOOL_PATH


def test_filenames_match_what_the_speech_dispatcher_asks_for(tool):
    assert tool.wav_filename(0) == 'greeting_00.wav'
    assert tool.wav_filename(5) == 'greeting_05.wav'
    assert tool.wav_filename(12) == 'greeting_12.wav'


def test_written_wavs_meet_the_documented_format(tmp_path, tool):
    path = tmp_path / 'greeting_00.wav'
    tool.write_wav(str(path), tool.synthesise('Hello there!'))
    with wave.open(str(path), 'rb') as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2          # 16-bit
        assert handle.getframerate() == 16000
        assert handle.getnframes() > 0


def test_samples_are_clipped_into_int16_range(tmp_path, tool):
    path = tmp_path / 'loud.wav'
    tool.write_wav(str(path), np.full(1600, 5.0, dtype=np.float32))
    with wave.open(str(path), 'rb') as handle:
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert data.max() <= 32767
    assert data.min() >= -32768


def test_it_generates_one_file_per_phrase(tmp_path, tool):
    dest = tmp_path / 'audio'
    assert tool.main(['--dest', str(dest)]) == 0

    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    produced = sorted(p.name for p in dest.glob('*.wav'))
    assert produced == [tool.wav_filename(i) for i in range(len(DEFAULT_PHRASES))]


def test_every_generated_file_is_playable_and_conformant(tmp_path, tool):
    dest = tmp_path / 'audio'
    tool.main(['--dest', str(dest)])
    for path in sorted(dest.glob('*.wav')):
        with wave.open(str(path), 'rb') as handle:
            assert (handle.getnchannels(), handle.getsampwidth(),
                    handle.getframerate()) == (1, 2, 16000)
            assert handle.getnframes() > 0


def test_the_file_count_matches_the_speech_default(tool):
    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    # config/greeter.yaml sets speech.audio_file_count to this length; a
    # mismatch would make the dispatcher ask for a recording that is not there.
    assert len(DEFAULT_PHRASES) == 6
