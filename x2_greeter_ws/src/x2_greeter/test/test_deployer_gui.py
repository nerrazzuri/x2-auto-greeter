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


# -- two languages -----------------------------------------------------------

def test_the_window_opens_in_whichever_language_is_set(app):
    from deployer.core import i18n

    before = i18n.language()
    try:
        i18n.set_language('en')
        english = Deployer()
        assert english.deploy_button.text() == 'Deploy'
        assert '**' not in english.problems.text()
        english.deleteLater()

        i18n.set_language('zh')
        chinese = Deployer()
        assert chinese.deploy_button.text() == '开始部署'
        chinese.deleteLater()
    finally:
        i18n.set_language(before)


def test_the_validation_message_follows_the_language(app):
    from deployer.core import i18n

    before = i18n.language()
    try:
        for code, expected in (('en', 'greetings'), ('zh', '句')):
            i18n.set_language(code)
            window = Deployer()
            window._set_rows(GOOD)
            assert expected in window.problems.text(), code
            window.deleteLater()
    finally:
        i18n.set_language(before)


def test_a_failure_is_reported_in_the_language_in_force(app):
    """The message a customer needs most is the one they must be able to
    read."""
    from deployer.core import i18n

    class _Outcome:
        ok = False
        identity = None
        startup_line = ''

    before = i18n.language()
    try:
        i18n.set_language('en')
        window = Deployer()
        window._on_done(_Outcome())
        assert 'Progress' in window.result.text()
        assert '过程记录' not in window.result.text()
        window.deleteLater()
    finally:
        i18n.set_language(before)


def test_switching_language_keeps_what_the_customer_typed(app, monkeypatch):
    """Losing a list of greetings to a button pressed out of curiosity would
    be unforgivable, so the new window inherits them."""
    from deployer.core import i18n

    before = i18n.language()
    try:
        i18n.set_language('zh')
        window = Deployer()
        window._set_rows(GOOD)
        window.venue.setText('Somewhere Mall')
        window._say('一行记录')

        created = {}
        real = Deployer

        def capture():
            created['window'] = real()
            return created['window']

        monkeypatch.setattr('deployer.gui.app.Deployer', capture)
        window._switch_language()

        fresh = created['window']
        assert i18n.language() == 'en'
        assert fresh._rows() == GOOD
        assert fresh.venue.text() == 'Somewhere Mall'
        assert '一行记录' in fresh.log.toPlainText()
        assert fresh.deploy_button.text() == 'Deploy'
    finally:
        i18n.set_language(before)


# -- the standard greetings are a fallback, not a draft ----------------------
#
# Pre-filling the table with them would let someone edit them, deploy, and
# believe the result was their own writing. They stay out of the table; an
# empty table means "use the standard ones", and the customer is asked first.

def test_the_table_starts_empty(app):
    window = Deployer()
    try:
        assert window._rows() == []
    finally:
        window.deleteLater()


def test_an_empty_table_is_offered_rather_than_refused(app):
    """Not an error to be corrected: it is the default, and the button says so
    instead of greying out with no explanation."""
    window = Deployer()
    try:
        assert window.deploy_button.isEnabled()
        assert '通用' in window.problems.text()
    finally:
        window.deleteLater()


def test_deploying_an_empty_table_asks_first(app, monkeypatch):
    """Greeting a customer's visitors in words they never saw is not something
    to do quietly."""
    window = Deployer()
    try:
        asked = {}

        def fake_exec(box):
            asked['text'] = box.text()
            asked['detail'] = box.informativeText()
            asked['buttons'] = [b.text() for b in box.buttons()]
            box.setResult(0)

        monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.exec', fake_exec)
        monkeypatch.setattr(
            'PyQt6.QtWidgets.QMessageBox.clickedButton', lambda _self: None)

        window._deploy()

        assert asked, '应该先问'
        assert '通用' in asked['text']
        assert asked['detail'].count('·') >= 3, '要给出具体会说什么'
        assert len(asked['buttons']) == 2
    finally:
        window.deleteLater()


def test_declining_the_standard_greetings_deploys_nothing(app, monkeypatch):
    window = Deployer()
    try:
        monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.exec', lambda _s: 0)
        monkeypatch.setattr(
            'PyQt6.QtWidgets.QMessageBox.clickedButton', lambda _s: None)
        started = []
        monkeypatch.setattr(window, '_start', lambda worker: started.append(worker))

        window._deploy()
        assert started == [], '客户说要自己写,就不该开始部署'
        assert window.deploy_button.isEnabled(), '按钮要还能再点'
    finally:
        window.deleteLater()


def test_the_standard_greetings_are_venue_neutral():
    """The KL Gateway Mall list names a mall and a company; handing those to a
    different customer would be worse than handing them nothing."""
    from deployer.core import phrases as P

    joined = ' '.join(P.defaults())
    assert joined, '内置问候语必须存在'
    assert 'KL Gateway' not in joined and 'Always Robot' not in joined


def test_the_standard_greetings_pass_their_own_checks():
    from deployer.core import phrases as P

    assert P.check(P.defaults()) == []


# -- signals that shadow Qt's own members ------------------------------------

def test_no_worker_signal_shadows_a_qobject_member():
    """A signal named `event` shadowed QObject.event(), the virtual Qt calls
    to dispatch events to an object. moveToThread then raised "native Qt
    signal is not callable" from inside Qt, left the thread half-moved, and
    the process aborted -- which the customer saw as the window vanishing the
    moment they pressed Deploy.

    Nothing about that is specific to `event`: any name QObject already uses
    does the same, and none of them fail until the program is running.
    """
    from PyQt6.QtCore import QObject, pyqtSignal

    from deployer.gui import app as gui

    workers = [value for value in vars(gui).values()
               if isinstance(value, type) and issubclass(value, QObject)
               and value is not QObject]
    assert workers, '没有找到任何 QObject 子类,测试本身失效了'

    taken = set(dir(QObject))
    for worker in workers:
        signals = [name for name, value in vars(worker).items()
                   if isinstance(value, type(pyqtSignal()))]
        clashes = [name for name in signals if name in taken]
        assert clashes == [], f'{worker.__name__} 的信号盖住了 QObject 的成员: {clashes}'


def test_a_worker_survives_being_moved_to_a_thread(app, monkeypatch):
    """The exact operation that aborted, and the line every worker goes
    through before it does any work.

    The assertion is on sys.excepthook, not on a raised exception: PyQt hands
    an exception escaping Qt's own call to excepthook and carries on, so
    moveToThread returned normally while the thread was left half-moved. A
    test that only checked for a raise would have passed throughout.
    """
    from PyQt6.QtCore import QThread

    from deployer.gui.app import DeployWorker

    caught = []
    monkeypatch.setattr(sys, 'excepthook',
                        lambda kind, value, tb: caught.append(value))

    thread = QThread()
    worker = DeployWorker(robot=None, plan=None)
    try:
        worker.moveToThread(thread)
        assert caught == [], f'Qt 内部抛了异常:{caught}'
        assert worker.thread() is thread
    finally:
        thread.quit()
        thread.wait(1000)
