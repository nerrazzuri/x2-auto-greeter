"""The window's own logic: what it enables, what it refuses, what it says.

Only the parts a customer can get wrong are covered. Qt's own widgets are not
under test; the wiring between the phrase table and the deploy button is,
because that wiring is the only thing standing between a mall and a robot
reading markdown asterisks out loud.

Skipped where PyQt6 is not installed, so the suite still runs on the robot and
in CI. Rendered offscreen, so it needs no display.
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip('PyQt6', reason='deployer GUI not installed here')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from PyQt6.QtWidgets import QApplication            # noqa: E402

from deployer.gui.app import Deployer               # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    w = Deployer()
    yield w
    w.deleteLater()


GOOD = ['Welcome to KL Gateway Mall!', "Hi! I'm X2."]


def test_an_empty_list_cannot_be_deployed(window):
    """A robot with no greetings is a robot that stands there silently, and
    the customer would have no way to tell that from a broken one."""
    assert not window.deploy_button.isEnabled()
    assert '一句' in window.problems.text()


def test_a_clean_list_enables_the_button_and_says_how_many(window):
    window._set_rows(GOOD)
    assert window.deploy_button.isEnabled()
    assert '2 句' in window.problems.text()


def test_markup_blocks_deployment_because_the_robot_would_read_it_aloud(window):
    window._set_rows(['Visit us at **UG**!'])
    assert not window.deploy_button.isEnabled()
    assert '符号' in window.problems.text()


def test_word_quotes_are_reported_as_fixable_and_the_fix_clears_them(window):
    window._set_rows(['Hi! I’m X2.'])
    assert '自动修正' in window.problems.text()
    window._autofix()
    assert window._rows() == ["Hi! I'm X2."]
    assert window.deploy_button.isEnabled()


def test_a_bad_row_is_marked_in_the_table_so_the_customer_can_see_which(window):
    window._set_rows(['Fine one!', 'Bad **one**!'])
    first = window.table.item(0, 0).background().color().alpha()
    second = window.table.item(1, 0).background().color().alpha()
    assert second > first, '有问题的那一行要有底色'


def test_importing_a_notepad_file_fills_the_table(window, tmp_path):
    path = tmp_path / 'greetings.txt'
    path.write_text('Hello there!\nWelcome!\n', encoding='utf-8')
    window._set_rows([])
    from deployer.core import phrases as P
    window._set_rows(P.load(path))
    assert window._rows() == ['Hello there!', 'Welcome!']
    assert window.deploy_button.isEnabled()


def test_the_venue_reaches_the_saved_file(window, tmp_path):
    from deployer.core import phrases as P

    window.venue.setText('Somewhere Mall')
    window._set_rows(GOOD)
    path = tmp_path / 'phrases.yaml'
    P.save(path, window._rows(), venue=window.venue.text())
    assert 'Somewhere Mall' in path.read_text(encoding='utf-8')
    assert P.load(path) == GOOD


def test_a_failure_points_at_the_log_that_is_actually_below_it(window):
    """The result line tells the customer where to look. It said 上面 while
    the log sits underneath, which sends them to the wrong half of the
    window at the exact moment they are lost."""
    class _Outcome:
        ok = False
        identity = None
        startup_line = ''

    window._on_done(_Outcome())
    assert '下面' in window.result.text()
    assert '上面' not in window.result.text()


def test_success_reports_what_the_robot_says_it_is_running(window):
    """Not "deployed" -- the line the robot itself produced. Everything this
    project has got wrong looked successful before that line was read."""
    class _Outcome:
        ok = True
        identity = 'agi · 序列号 1423225023973'
        startup_line = 'camera=stereo backend=canned speech_tier=tts'

    window._on_done(_Outcome())
    assert 'camera=stereo' in window.result.text()
    assert 'backend=canned' in window.result.text()
