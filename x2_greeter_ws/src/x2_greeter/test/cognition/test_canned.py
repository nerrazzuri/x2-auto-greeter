import random
from pathlib import Path

import pytest

from x2_greeter.cognition.canned import DEFAULT_PHRASES, CannedBackend, load_phrases
from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import SceneContext, Verdict

CTX = SceneContext(distance_m=2.0, center_offset=0.0)
PHRASES_YAML = Path(__file__).resolve().parents[2] / 'config' / 'phrases.yaml'


def test_canned_backend_satisfies_the_protocol():
    assert isinstance(CannedBackend(), GreetingBackend)


def test_it_always_confirms_the_person():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.person_present is True


def test_it_returns_a_phrase_from_the_list():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.greeting in DEFAULT_PHRASES


def test_it_leaves_the_gesture_unset_so_the_selector_chooses():
    assert CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX).gesture is None


def test_it_labels_its_source():
    assert CannedBackend().confirm_and_compose(None, CTX).source == 'canned'


def test_it_does_not_claim_to_know_the_orientation():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.facing_robot is False
    assert verdict.confidence == 0.0


def test_it_does_not_repeat_itself():
    backend = CannedBackend(rng=random.Random(3))
    previous = None
    for _ in range(30):
        greeting = backend.confirm_and_compose(None, CTX).greeting
        assert greeting != previous
        previous = greeting


def test_it_copes_with_a_single_phrase():
    backend = CannedBackend(['Hi.'], rng=random.Random(0))
    assert backend.confirm_and_compose(None, CTX).greeting == 'Hi.'
    assert backend.confirm_and_compose(None, CTX).greeting == 'Hi.'


def test_it_ignores_the_frame_entirely():
    backend = CannedBackend(rng=random.Random(0))
    assert isinstance(backend.confirm_and_compose(None, CTX), Verdict)


def test_it_rejects_an_empty_phrase_list():
    with pytest.raises(ValueError, match='at least one'):
        CannedBackend([])


def test_the_shipped_phrase_file_loads():
    phrases = load_phrases(PHRASES_YAML)
    assert len(phrases) == len(DEFAULT_PHRASES)
    assert all(isinstance(p, str) and p.strip() for p in phrases)


def test_the_shipped_phrase_file_matches_the_module_default():
    # Task 17 generates greeting_NN.wav from this file by index; a drift between
    # the two would silently deploy the wrong recordings.
    assert load_phrases(PHRASES_YAML) == DEFAULT_PHRASES


def test_load_phrases_rejects_a_file_with_no_phrases(tmp_path):
    bad = tmp_path / 'phrases.yaml'
    bad.write_text('phrases: []\n', encoding='utf-8')
    with pytest.raises(ValueError, match='at least one'):
        load_phrases(bad)


def test_backend_unavailable_is_an_exception():
    assert issubclass(BackendUnavailable, Exception)
