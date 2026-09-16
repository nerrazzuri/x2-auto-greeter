"""The phrase list a customer edits, and everything they can get wrong with it.

This is the one part of the deployer a non-engineer touches directly, so the
tests are written from the mistakes rather than the API: Word's curly quotes,
numbering pasted out of a brief, a line left blank, the same greeting entered
twice. Each has to be caught here, on the laptop, with a sentence naming the
line -- because the robot's own handling is to log one warning and greet the
customer's mall in the built-in English jokes instead.

No ROS and no robot: plain files and strings.
"""
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import phrases as P  # noqa: E402


REAL = [
    "Hi there! I'm X2 from Always Robots. Welcome to KL Gateway Mall!",
    "Hello! I'm X2, your friendly robot. Welcome!",
]


# -- what a customer pastes in -----------------------------------------------

def test_curly_quotes_out_of_word_are_caught_and_repaired():
    pasted = ['Hi! I’m X2, and it’s nice to meet you.']
    problem = P.check(pasted)[0]
    assert problem.line == 1
    assert problem.fixable
    assert 'Word' in problem.message

    assert P.apply_fixes(pasted) == ["Hi! I'm X2, and it's nice to meet you."]
    assert P.check(P.apply_fixes(pasted)) == []


def test_an_em_dash_becomes_a_comma():
    assert P.apply_fixes(['Shopping — or just looking?']) == \
        ['Shopping , or just looking?']


def test_numbering_pasted_from_the_brief_is_stripped():
    pasted = ['1. Welcome to the mall!', '2) Nice to meet you!', '3、你好!']
    assert any('编号' in p.message for p in P.check(pasted))
    assert P.apply_fixes(pasted) == [
        'Welcome to the mall!', 'Nice to meet you!', '你好!']


def test_a_decimal_at_the_start_is_not_mistaken_for_numbering():
    kept = ['1.5 metres is close enough for a chat!']
    assert P.check(kept) == []
    assert P.apply_fixes(kept) == kept


def test_a_blank_line_is_reported_and_dropped():
    assert any('空的' in p.message for p in P.check(['Hello!', '   ', 'Hi!']))
    assert P.apply_fixes(['Hello!', '   ', 'Hi!']) == ['Hello!', 'Hi!']


def test_the_same_greeting_twice_is_reported_but_not_silently_removed():
    """Deleting one is the customer's call: the duplicate may be a typo in one
    of the two, and dropping the wrong one loses their wording."""
    twice = ['Welcome!', 'welcome!']
    problem = [p for p in P.check(twice) if '重复' in p.message][0]
    assert problem.line == 2 and not problem.fixable
    assert len(P.apply_fixes(twice)) == 2


def test_markup_is_reported_but_needs_a_human():
    problem = [p for p in P.check(['Visit us at **UG**!']) if '符号' in p.message][0]
    assert not problem.fixable, 'removing the stars might not be what they meant'


def test_a_greeting_nobody_would_stay_for_is_refused():
    assert any('太长' in p.message for p in P.check(['Hello! ' + 'x' * 300]))


def test_an_empty_list_is_the_one_problem_reported_on_its_own():
    problems = P.check([])
    assert len(problems) == 1
    assert '一句' in problems[0].message


def test_a_clean_list_has_nothing_to_say():
    assert P.check(REAL) == []


# -- reading whatever the customer sends ------------------------------------

def test_a_plain_text_file_typed_in_notepad(tmp_path):
    path = tmp_path / 'greetings.txt'
    path.write_text('# 我们的问候语\n\nHello there!\nWelcome to the mall!\n',
                    encoding='utf-8')
    assert P.load(path) == ['Hello there!', 'Welcome to the mall!']


def test_a_text_file_saved_by_windows_notepad_with_a_bom(tmp_path):
    """Notepad writes UTF-8 with a BOM by default, and the BOM would otherwise
    ride along on the first greeting."""
    path = tmp_path / 'greetings.txt'
    path.write_bytes('﻿Hello there!\n'.encode('utf-8'))
    assert P.load(path) == ['Hello there!']


def test_the_yaml_a_previous_deployment_produced_reads_back(tmp_path):
    path = tmp_path / 'phrases.yaml'
    P.save(path, REAL, venue='KL Gateway Mall')
    assert P.load(path) == REAL


def test_a_yaml_file_missing_its_phrases_block_says_so(tmp_path):
    path = tmp_path / 'phrases.yaml'
    path.write_text('greetings:\n  - Hello\n', encoding='utf-8')
    with pytest.raises(ValueError, match='phrases'):
        P.load(path)


# -- writing the file the robot loads ---------------------------------------

def test_apostrophes_survive_the_round_trip(tmp_path):
    """The trap that breaks a hand-written file: a single-quoted YAML scalar
    containing an apostrophe is a parse error, and English greetings are full
    of them."""
    path = tmp_path / 'phrases.yaml'
    P.save(path, ["Hi! I'm X2. Don't be shy — say hello!"])
    loaded = P.load(path)
    assert loaded == ["Hi! I'm X2. Don't be shy , say hello!"]


def test_double_quotes_inside_a_greeting_survive(tmp_path):
    path = tmp_path / 'phrases.yaml'
    P.save(path, ['They call me "X2".'])
    assert P.load(path) == ['They call me "X2".']


def test_what_is_written_is_what_the_robot_loads(tmp_path):
    """The deployer's output has to satisfy the node's own loader, not just
    this module's."""
    from x2_greeter.cognition.canned import load_phrases

    path = tmp_path / 'phrases.yaml'
    P.save(path, REAL, venue='Somewhere Mall')
    assert list(load_phrases(path)) == REAL


def test_the_generated_file_carries_the_venue_for_whoever_opens_it_next(tmp_path):
    path = tmp_path / 'phrases.yaml'
    P.save(path, REAL, venue='KL Gateway Mall')
    assert 'KL Gateway Mall' in path.read_text(encoding='utf-8')
    assert yaml.safe_load(path.read_text(encoding='utf-8'))['phrases'] == REAL
