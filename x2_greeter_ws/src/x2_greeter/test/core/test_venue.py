"""A venue profile is the authority over what the robot may assert.

The camera can tell the robot there are clothes on a rail. It cannot tell it
whether the shop takes returns, where the fitting rooms are, or whether the
sale ends on Sunday. Those come from the profile or they are not said at all
-- so a malformed profile must refuse to load rather than silently produce a
robot with no facts and no forbidden topics.
"""
from pathlib import Path

import pytest

from x2_greeter.core.venue import VenueError, VenueProfile, load_venue, parse_venue

VENUE_DIR = Path(__file__).resolve().parents[2] / 'config' / 'venues'

MINIMAL = {
    'venue': {
        'kind': 'clothing_store',
        'role': 'a greeter standing near the entrance of a clothing store',
        'language_default': 'en',
        'opening': {'en': 'Hello! Welcome in.', 'zh': '你好！欢迎光临。'},
        'facts': ['The fitting rooms are at the back on the left.'],
        'topics_encouraged': ['what the customer is looking for'],
        'topics_forbidden': ['prices', 'stock levels'],
        'deflect_to_human': 'A staff member can help you with that.',
    }
}


def test_a_minimal_profile_parses():
    profile = parse_venue(MINIMAL)
    assert isinstance(profile, VenueProfile)
    assert profile.kind == 'clothing_store'
    assert profile.facts == ('The fitting rooms are at the back on the left.',)
    assert profile.topics_forbidden == ('prices', 'stock levels')


def test_the_opening_line_is_available_per_language():
    profile = parse_venue(MINIMAL)
    assert profile.opening_for('en') == 'Hello! Welcome in.'
    assert profile.opening_for('zh') == '你好！欢迎光临。'
    # An unsupported language falls back to the profile default, never crashes.
    assert profile.opening_for('ms') == 'Hello! Welcome in.'


@pytest.mark.parametrize('missing', [
    'kind', 'role', 'opening', 'facts', 'topics_forbidden', 'deflect_to_human'])
def test_a_profile_missing_a_required_key_refuses_to_load(missing):
    doc = {'venue': dict(MINIMAL['venue'])}
    del doc['venue'][missing]
    with pytest.raises(VenueError) as exc:
        parse_venue(doc)
    assert missing in str(exc.value)


def test_a_profile_with_no_opening_for_the_default_language_refuses_to_load():
    doc = {'venue': dict(MINIMAL['venue'], opening={'zh': '你好'})}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_a_profile_with_an_unsupported_default_language_refuses_to_load():
    doc = {'venue': dict(MINIMAL['venue'], language_default='ms')}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_a_profile_with_no_facts_refuses_to_load():
    # An empty facts list is not a venue with nothing to say; it is a profile
    # someone forgot to fill in, and it would produce a robot that invents.
    doc = {'venue': dict(MINIMAL['venue'], facts=[])}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_the_prompt_carries_the_facts_and_the_forbidden_topics():
    prompt = parse_venue(MINIMAL).to_prompt()
    assert 'The fitting rooms are at the back on the left.' in prompt
    assert 'prices' in prompt
    assert 'stock levels' in prompt
    assert 'A staff member can help you with that.' in prompt


@pytest.mark.parametrize('name', ['clothing_store', 'mall_atrium'])
def test_the_shipped_venue_profiles_load(name):
    profile = load_venue(VENUE_DIR / f'{name}.yaml')
    assert profile.kind == name
    assert profile.facts
    assert profile.opening_for('en')
    assert profile.opening_for('zh')


def test_loading_a_missing_profile_raises_venue_error():
    with pytest.raises(VenueError):
        load_venue(VENUE_DIR / 'no_such_venue.yaml')
