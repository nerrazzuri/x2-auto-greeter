"""Names the customer types, and the file they end up with.

The window asks for a name and nothing else -- no directory, no extension, no
filter -- so the name is the only thing standing between a customer and a file
somewhere unintended. Everything here is about that gap: a name is one
component of a path, never a path, and Windows refuses a handful of them for
reasons that predate all of us.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import library                              # noqa: E402


@pytest.fixture(autouse=True)
def somewhere_harmless(monkeypatch, tmp_path):
    """Never the real ~/.x2-deployer: these tests write and delete."""
    monkeypatch.setattr(library, 'directory', lambda: tmp_path / 'phrases')
    monkeypatch.setattr(library, '_pointer', lambda: tmp_path / 'last.txt')


def test_a_plain_name_becomes_one_file_in_our_own_directory():
    target = library.path_for('KL Gateway Mall')
    assert target.name == 'KL Gateway Mall.yaml'
    assert target.parent == library.directory()


def test_a_name_that_already_says_yaml_does_not_say_it_twice():
    assert library.path_for('phrases.yaml').name == 'phrases.yaml'
    assert library.path_for('phrases.YML').name == 'phrases.yaml'


@pytest.mark.parametrize('name', [
    '../../etc/passwd',                 # the one that matters
    'a/b',
    'a\\b',
    'C:evil',
    'star*',
    'pipe|',
    'quote"',
    'question?',
])
def test_a_name_that_is_really_a_path_is_refused(name):
    """The whole point of asking for a name: it cannot reach outside."""
    with pytest.raises(ValueError):
        library.path_for(name)


@pytest.mark.parametrize('name', ['', '   ', '.', '..', '.hidden'])
def test_empty_and_dotted_names_are_refused(name):
    with pytest.raises(ValueError):
        library.path_for(name)


@pytest.mark.parametrize('name', ['CON', 'nul', 'Com1', 'LPT9'])
def test_the_names_windows_refuses_are_refused_here(name):
    """On Windows these cannot be created whatever the extension, and the
    customer would be told nothing useful by the operating system."""
    with pytest.raises(ValueError):
        library.path_for(name)


def test_a_trailing_dot_is_refused():
    """Windows drops it silently, so the file is not where the name says."""
    with pytest.raises(ValueError):
        library.path_for('trailing.')


def test_surrounding_spaces_are_trimmed_rather_than_refused():
    """A space before or after is a typo, not a decision. Refusing it would
    be pedantry; keeping it would make the file impossible to find."""
    assert library.path_for('  Mall  ').name == 'Mall.yaml'


def test_a_very_long_name_is_refused():
    with pytest.raises(ValueError):
        library.path_for('x' * (library.LONGEST + 1))
    assert library.path_for('x' * library.LONGEST)


def test_every_refusal_says_something_a_customer_can_act_on():
    for bad in ('', 'a/b', 'CON', '.', 'x' * 99, 'trailing.'):
        with pytest.raises(ValueError) as caught:
            library.path_for(bad)
        message = str(caught.value)
        assert message and not message.startswith('library.'), \
            f'{bad!r} 的报错没有翻译'


def test_the_file_used_last_is_remembered_and_forgotten():
    target = library.path_for('Mall')
    target.parent.mkdir(parents=True)
    target.write_text('phrases: []\n', encoding='utf-8')

    assert library.last() is None
    library.remember(target)
    assert library.last() == target.resolve()

    library.forget()
    assert library.last() is None


def test_a_remembered_file_that_has_been_deleted_is_not_offered():
    """Customers move and delete things. Reopening a file that is gone must
    not be an error on startup."""
    target = library.path_for('Gone')
    target.parent.mkdir(parents=True)
    target.write_text('phrases: []\n', encoding='utf-8')
    library.remember(target)
    target.unlink()

    assert library.last() is None


def test_remembering_never_raises(monkeypatch, tmp_path):
    """It is a convenience. A read-only home must not break saving."""
    monkeypatch.setattr(library, '_pointer',
                        lambda: tmp_path / 'no' / 'such' / 'dir' / 'x.txt')

    def refuse(*args, **kwargs):
        raise OSError('read-only')

    monkeypatch.setattr(Path, 'mkdir', refuse)
    library.remember(tmp_path / 'whatever.yaml')      # must not raise
    assert library.last() is None


def test_the_open_dialog_never_starts_inside_the_program():
    """Frozen, the program lives in a temporary directory full of files that
    look like greeting lists and disappear when it closes."""
    assert library.start_directory() == Path.home()

    library.directory().mkdir(parents=True)
    (library.directory() / 'Mall.yaml').write_text('phrases: []\n')
    assert library.start_directory() == library.directory()

    elsewhere = Path.home() / 'Documents' / 'mine.yaml'
    library.remember(elsewhere)
    if elsewhere.is_file():                           # only if it really is
        assert library.start_directory() == elsewhere.parent


def test_saved_lists_the_newest_first():
    library.directory().mkdir(parents=True)
    import os
    import time
    for index, name in enumerate(('old', 'new')):
        path = library.path_for(name)
        path.write_text('phrases: []\n', encoding='utf-8')
        os.utime(path, (time.time() + index, time.time() + index))
    assert [p.stem for p in library.saved()] == ['new', 'old']
