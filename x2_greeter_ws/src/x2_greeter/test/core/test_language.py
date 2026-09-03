"""The language state is explicit and follows the person (spec section 10).

Malay is deferred to a later phase; a Malay tag must be treated exactly like
any other unsupported tag -- ignored, not crashed on, and never switched to.
"""
import pytest

from x2_greeter.core.language import (
    ALLOWED_LANGUAGES, DEFAULT_LANGUAGE, LanguagePolicy, normalise_language)


def test_this_phase_ships_english_and_chinese_only():
    assert ALLOWED_LANGUAGES == ('en', 'zh')
    assert DEFAULT_LANGUAGE == 'en'


@pytest.mark.parametrize('raw, expected', [
    ('en', 'en'), ('EN', 'en'), ('en-US', 'en'), ('en_GB', 'en'),
    ('zh', 'zh'), ('zh-CN', 'zh'), ('ZH_Hans', 'zh'), (' zh ', 'zh'),
])
def test_regional_tags_normalise_to_the_base_language(raw, expected):
    assert normalise_language(raw) == expected


@pytest.mark.parametrize('raw', ['ms', 'ms-MY', 'ja', '', None, 'unknown', 'e'])
def test_unsupported_or_missing_tags_normalise_to_none(raw):
    assert normalise_language(raw) is None


def test_the_language_holds_when_detection_is_not_confident():
    policy = LanguagePolicy(switch_confidence=0.7)
    assert policy.next_language('en', 'zh', 0.69) == 'en'


def test_the_language_follows_the_person_when_detection_is_confident():
    policy = LanguagePolicy(switch_confidence=0.7)
    assert policy.next_language('en', 'zh', 0.7) == 'zh'
    assert policy.next_language('zh', 'en', 0.95) == 'en'


def test_an_unsupported_detection_never_switches_however_confident():
    # Malay is deferred: a confident 'ms' must not move the state off English.
    policy = LanguagePolicy()
    assert policy.next_language('en', 'ms', 1.0) == 'en'
    assert policy.next_language('en', None, 1.0) == 'en'


def test_an_invalid_current_language_falls_back_to_the_default():
    policy = LanguagePolicy(default='en')
    assert policy.next_language('ms', None, 1.0) == 'en'
