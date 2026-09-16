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

from PyQt6.QtWidgets import QApplication              # noqa: E402

from deployer.core import assets, library           # noqa: E402
from deployer.core import phrases as P              # noqa: E402
from deployer.gui.app import Deployer, FAIL_RED, OK_GREEN  # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def the_model_is_on_this_machine(monkeypatch, tmp_path):
    """Every window in this file opens on a machine that has the vision model.

    Without this the suite's result depends on whether this particular laptop
    happens to have the weights in one of the six places `assets` looks, and
    it depends on it twice over. The deploy button is dead without them, so
    every assertion about that button flips; and _deploy() opens a modal
    QMessageBox.warning, which is a static method that patching
    QMessageBox.exec does not touch, so offscreen the suite hangs rather than
    fails while it waits for a click that cannot come.

    Both of those were real: two tests here passed for months on machines that
    had the model and hung on a fresh checkout. The tests that are actually
    about the model say so for themselves, below.
    """
    monkeypatch.setattr(assets, 'locate',
                        lambda extra=None: assets.Weights(str(tmp_path), []))


@pytest.fixture(autouse=True)
def a_library_of_its_own(monkeypatch, tmp_path):
    """Never the real ~/.x2-deployer/phrases.

    The window reopens the file used last, so without this the suite's result
    depends on which greeting file the developer happened to save yesterday --
    and saving in a test would leave a file behind that changes the next run.
    """
    monkeypatch.setattr(library, 'directory', lambda: tmp_path / 'phrases')
    monkeypatch.setattr(library, '_pointer', lambda: tmp_path / 'last.txt')


@pytest.fixture
def window(app):
    w = Deployer(watch_robot=False)
    yield w
    w.deleteLater()


GOOD = ['Welcome to KL Gateway Mall!', "Hi! I'm X2."]


def log_text(window) -> str:
    """Everything in the progress list, as one string."""
    return '\n'.join(window.log.item(i).text()
                     for i in range(window.log.count()))


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
        english = Deployer(watch_robot=False)
        assert english.deploy_button.text() == 'Deploy'
        assert '**' not in english.problems.text()
        english.deleteLater()

        i18n.set_language('zh')
        chinese = Deployer(watch_robot=False)
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
            window = Deployer(watch_robot=False)
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
        window = Deployer(watch_robot=False)
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
        window = Deployer(watch_robot=False)
        window._set_rows(GOOD)
        window.venue.setText('Somewhere Mall')
        window._say('一行记录')

        created = {}
        real = Deployer

        def capture(watch_robot=True):
            created['window'] = real(watch_robot=False)
            return created['window']

        monkeypatch.setattr('deployer.gui.app.Deployer', capture)
        window._switch_language()

        fresh = created['window']
        assert i18n.language() == 'en'
        assert fresh._rows() == GOOD
        assert fresh.venue.text() == 'Somewhere Mall'
        assert '一行记录' in log_text(fresh)
        assert fresh.deploy_button.text() == 'Deploy'
    finally:
        i18n.set_language(before)


# -- the standard greetings are a fallback, not a draft ----------------------
#
# Pre-filling the table with them would let someone edit them, deploy, and
# believe the result was their own writing. They stay out of the table; an
# empty table means "use the standard ones", and the customer is asked first.

def test_the_table_starts_empty(app):
    window = Deployer(watch_robot=False)
    try:
        assert window._rows() == []
    finally:
        window.deleteLater()


def test_an_empty_table_is_offered_rather_than_refused(app):
    """Not an error to be corrected: it is the default, and the button says so
    instead of greying out with no explanation."""
    window = Deployer(watch_robot=False)
    try:
        assert window.deploy_button.isEnabled()
        assert '通用' in window.problems.text()
    finally:
        window.deleteLater()


def _no_model(monkeypatch):
    """A machine that has never downloaded the weights."""
    monkeypatch.setattr(
        assets, 'locate',
        lambda extra=None: assets.Weights(None, list(assets.NAMES)))


def test_without_the_model_the_deploy_button_is_dead(app, monkeypatch):
    """Not a warning after the click -- the button itself.

    Deploying without the model produces a robot that starts, reports itself
    healthy, and then walks past every person in front of it, because it falls
    back to a 2005 detector. Nothing about that looks wrong from outside, so
    it is worth refusing rather than warning about.
    """
    _no_model(monkeypatch)
    window = Deployer(watch_robot=False)
    try:
        window._set_rows(GOOD)
        assert window._phrases_ok, '问候语本身没问题'
        assert not window.deploy_button.isEnabled(), '没有模型,部署键应该是灰的'
        assert window.deploy_button.toolTip(), '灰掉了就要说为什么'
    finally:
        window.deleteLater()


def test_downloading_the_model_brings_the_deploy_button_back(app, monkeypatch, tmp_path):
    """The grey button has to come back on its own once the reason is gone."""
    _no_model(monkeypatch)
    window = Deployer(watch_robot=False)
    try:
        window._set_rows(GOOD)
        assert not window.deploy_button.isEnabled()

        monkeypatch.setattr(assets, 'locate',
                            lambda extra=None: assets.Weights(str(tmp_path), []))
        window._find_weights()

        assert window.deploy_button.isEnabled(), '下载完就该能部署了'
        assert window.deploy_button.toolTip() == '', '理由没了,提示也该没了'
    finally:
        window.deleteLater()


def test_the_model_alone_is_not_enough_to_enable_deployment(app, monkeypatch):
    """Both conditions, not the last one to be evaluated."""
    window = Deployer(watch_robot=False)          # the model is present
    try:
        window._set_rows(['Visit us at **UG**!'])  # but this cannot be spoken
        assert not window.deploy_button.isEnabled()
    finally:
        window.deleteLater()


def test_deploying_without_the_model_says_so_and_stops(app, monkeypatch):
    """The branch the two tests below used to fall into by accident."""
    warned = []
    monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.warning',
                        lambda *a, **k: warned.append(a[1:3]))
    # Every other modal too, so that a version of _deploy() which warns and
    # then carries on fails here instead of hanging on the next dialog.
    monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.exec', lambda _s: 0)
    monkeypatch.setattr(
        'PyQt6.QtWidgets.QMessageBox.clickedButton', lambda _s: None)
    window = Deployer(watch_robot=False)
    try:
        # Real greetings, so the only thing that can stop this deployment is
        # the missing model. With an empty table the confirmation dialog stops
        # it too, and the test would pass whatever _deploy() did about weights.
        window._set_rows(GOOD)
        window._weights_dir = None
        started = []
        monkeypatch.setattr(window, '_start', lambda w: started.append(w))

        window._deploy()

        assert warned, '没有模型就该说一声'
        assert started == [], '没有模型不能开始部署'
    finally:
        window.deleteLater()


def test_deploying_an_empty_table_asks_first(app, monkeypatch):
    """Greeting a customer's visitors in words they never saw is not something
    to do quietly."""
    window = Deployer(watch_robot=False)
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
    window = Deployer(watch_robot=False)
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


# -- watching for the robot --------------------------------------------------

def test_the_lamp_and_wording_follow_the_state(app):
    """Four states, four fixes. The colour is what reads from a metre away,
    which is where somebody stands while plugging a cable in.

    The label is a state, not an explanation: "this computer's wired address
    is 192.168.5.19, which is not on the same network as..." wrapped to six
    lines and pushed the panel out of shape. That text is in the log, in the
    fix dialog and on hover.
    """
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        seen = {}
        for state in (watch.NO_LINK, watch.WRONG_SUBNET, watch.NO_SSH, watch.READY):
            identity = 'agi · 1423' if state == watch.READY else None
            window._on_status(watch.Status(state, f'detail for {state}', identity))
            seen[state] = (window.lamp.styleSheet(), window.identity.text(),
                           window.identity.toolTip())

        assert len({v[0] for v in seen.values()}) == 4, '四种状态要有四种颜色'
        assert len({v[1] for v in seen.values()}) == 4, '四种状态要有四种说法'
        for state, (_, label, _tip) in seen.items():
            assert len(label) < 30, f'{state} 的状态标签太长了:{label!r}'
        assert 'agi' in seen[watch.READY][2], '是哪一台,鼠标悬停要看得到'
    finally:
        window.deleteLater()


def test_the_fix_button_appears_only_when_there_is_something_to_fix(app):
    """The instructions run to eight lines and name a settings panel. In the
    window they would push everything else off screen; behind a button they
    are there for the customer who is stuck."""
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        window._on_status(watch.Status(watch.WRONG_SUBNET, '网段不对\n改这里:…'))
        assert window.fix_button.isVisible() or window.fix_button.isVisibleTo(window)

        window._on_status(watch.Status(watch.READY, '好了', 'agi'))
        assert not window.fix_button.isVisibleTo(window)
    finally:
        window.deleteLater()


def test_the_same_news_does_not_fill_the_log(app):
    """Polling every two seconds and logging each time would bury the
    deployment's own output inside a minute."""
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        window._throttle = watch.Throttle(repeat_s=1000)
        for _ in range(10):
            window._on_status(watch.Status(watch.NO_LINK, '没有网线'))
        assert log_text(window).count('没有网线') == 1
    finally:
        window.deleteLater()


def test_the_watcher_thread_starts_and_stops_cleanly(app):
    """A QThread whose owner is collected before it is stopped aborts the
    process -- which is what a customer would see as the program crashing on
    exit."""
    window = Deployer(watch_robot=True)
    try:
        assert window._watch_thread.isRunning()
        window.stop_watching()
        assert not window._watch_thread.isRunning()
        window.stop_watching()               # twice must be safe
    finally:
        window.deleteLater()


# -- removing it from a robot ------------------------------------------------

def test_uninstalling_asks_first_and_says_what_goes(app, monkeypatch):
    """It deletes a customer's greetings and their automatic start. Asking is
    the minimum; saying what survives is what stops them worrying."""
    window = Deployer(watch_robot=False)
    try:
        asked = {}
        monkeypatch.setattr(
            'PyQt6.QtWidgets.QMessageBox.exec',
            lambda box: asked.update(text=box.text(), title=box.windowTitle()))
        monkeypatch.setattr(
            'PyQt6.QtWidgets.QMessageBox.clickedButton', lambda _s: None)
        started = []
        monkeypatch.setattr(window, '_start', started.append)

        window._uninstall()

        assert asked, '必须先问'
        assert '完全删除' in asked['text']
        assert '不受影响' in asked['text'], '要说清楚什么不会被动'
        assert started == [], '点了取消就不该开始'
    finally:
        window.deleteLater()


def test_uninstall_is_not_one_of_the_numbered_steps(app):
    """It sat at the bottom of the deploy panel, inside the 1-2-3 flow, where
    it read as a fourth step. Flattening it did not help -- the problem was
    where it was, not how loud."""
    window = Deployer(watch_robot=False)
    try:
        titles = [a.text() for a in window.more_button.menu().actions()]
        assert any('卸载' in x or 'Remove' in x for x in titles)
        assert not hasattr(window, 'uninstall_button')
    finally:
        window.deleteLater()


def test_uninstall_is_offered_only_once_a_robot_is_answering(app):
    """Offering it with no robot there produces a failure the customer then
    has to interpret."""
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        assert not window.uninstall_action.isEnabled()
        window._on_status(watch.Status(watch.READY, '好了', 'agi'))
        assert window.uninstall_action.isEnabled()
        window._on_status(watch.Status(watch.NO_LINK, '没网线'))
        assert not window.uninstall_action.isEnabled()
    finally:
        window.deleteLater()


def test_a_finished_uninstall_says_the_robot_is_otherwise_untouched(app):
    class _Outcome:
        ok = True

    window = Deployer(watch_robot=False)
    try:
        window._on_uninstalled(_Outcome())
        assert '不受影响' in window.result.text()
        assert window.deploy_button.isEnabled(), '卸载完还要能再装回去'
    finally:
        window.deleteLater()


def test_every_log_row_states_its_own_colour(app):
    """A row with no explicit foreground takes whatever the palette gives it,
    and on the customer's desktop that turned ordinary progress lines red."""
    from PyQt6.QtGui import QColor

    window = Deployer(watch_robot=False)
    try:
        for kind in ('info', 'ok', 'fail', 'warn', 'something-new'):
            window._say(f'line for {kind}', kind)

        colours = set()
        for i in range(window.log.count()):
            brush = window.log.item(i).foreground()
            assert brush.style() != 0, '每一行都要自己指定颜色'
            colours.add(brush.color().name())
        assert QColor(window.LOG_COLOURS['ok']).name() in colours
        assert QColor(window.LOG_COLOURS['fail']).name() in colours
    finally:
        window.deleteLater()


def test_deploying_offers_the_two_outcomes_rather_than_a_checkbox(app):
    """"Start automatically when the robot boots" asked the customer to decide
    before they knew they were being asked. Two sentences at the moment of
    pressing say what each one leaves behind."""
    window = Deployer(watch_robot=False)
    try:
        assert not hasattr(window, 'autostart'), '复选框应该没有了'
        actions = window.deploy_button.menu().actions()
        assert len(actions) == 2

        # Short enough to scan...
        for action in actions:
            assert len(action.text()) <= 20, action.text()
        # ...with what it means still reachable.
        tips = ' '.join(a.toolTip() for a in actions)
        assert '开机' in tips or 'boots' in tips
        assert '关机' in tips or 'powered off' in tips
        assert window.deploy_button.menu().toolTipsVisible(), \
            'Qt 默认不显示菜单项的悬停说明'
    finally:
        window.deleteLater()


def test_the_choice_reaches_the_plan(app, monkeypatch):
    window = Deployer(watch_robot=False)
    try:
        window._set_rows(GOOD)
        window._weights_dir = '/tmp'
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeWorker(QObject):
            reported = pyqtSignal(object)
            done = pyqtSignal(object)

        plans = []

        def capture(robot, plan):
            plans.append(plan)
            return FakeWorker()

        monkeypatch.setattr('deployer.gui.app.DeployWorker', capture)
        monkeypatch.setattr(window, '_start', lambda _w: None)

        window._deploy(True)
        window._deploy(False)
        assert [p.install_service for p in plans] == [True, False]
    finally:
        window.deleteLater()


# -- while a deployment is running -------------------------------------------

def test_the_watcher_goes_quiet_during_a_deployment(app):
    """Its lines every ten seconds would interleave with the steps the
    customer is actually watching."""
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        window._deploying = True
        for _ in range(3):
            window._on_status(watch.Status(watch.WRONG_SUBNET, '网段不对'))
        assert '网段不对' not in log_text(window)

        window._deploying = False
        window._on_status(watch.Status(watch.WRONG_SUBNET, '网段不对'))
        assert '网段不对' in log_text(window), '部署结束后要恢复'
    finally:
        window.deleteLater()


def test_the_lamp_reports_the_outcome_of_the_deployment(app):
    """Green or red where the customer is already looking, rather than one
    line among thirty in the log."""
    class _Outcome:
        def __init__(self, ok):
            self.ok = ok
            self.identity = 'agi'
            self.startup_line = 'camera=stereo'

    window = Deployer(watch_robot=False)
    try:
        window._on_done(_Outcome(True))
        assert OK_GREEN in window.lamp.styleSheet()
        green_words = window.identity.text()

        window._on_done(_Outcome(False))
        assert FAIL_RED in window.lamp.styleSheet()
        assert window.identity.text() != green_words
    finally:
        window.deleteLater()


def test_uninstall_is_not_offered_mid_deployment(app):
    from deployer.core import watch

    window = Deployer(watch_robot=False)
    try:
        window._deploying = True
        window._on_status(watch.Status(watch.READY, '好了', 'agi'))
        assert not window.uninstall_action.isEnabled()
    finally:
        window.deleteLater()


def test_the_photograph_ships_and_loads(app):
    """The picture is a file now, not a drawing, so it can go missing or
    arrive corrupt in a way a drawing never could."""
    from deployer.gui import robot_art

    assert robot_art.path().exists(), '照片没跟着仓库一起走'
    picture = robot_art.photograph()
    assert not picture.isNull(), '照片读不出来'
    assert picture.width() > 200 and picture.height() > 200


def test_the_frozen_build_puts_the_photograph_where_the_window_looks(monkeypatch):
    """The one failure that source runs cannot show: build.py and robot_art
    have to agree on a directory inside the bundle, or the customer's window
    opens with a hole in it."""
    sys.path.insert(0, str(REPO / 'tools' / 'deployer'))
    import build

    from deployer.gui import robot_art

    monkeypatch.setattr(sys, '_MEIPASS', '/tmp/bundle', raising=False)
    looked_in = robot_art.path()

    packed = build._artwork()
    assert len(packed) == 1
    source, _, target = packed[0][len('--add-data='):].rpartition(build.SEPARATOR)
    assert Path(source).name == looked_in.name
    assert looked_in.parent.name == target


def test_the_robot_fills_the_rest_of_the_left_column(window):
    """The column holds nothing else that can grow, so without this the
    window ends with a tall empty gap beside the greeting table."""
    from PyQt6.QtWidgets import QSizePolicy

    portrait = window.portrait
    assert portrait.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding
    assert portrait.minimumSizeHint().height() == 0, '窗口矮的时候要能让位'

    tall = portrait.parentWidget().layout()
    index = tall.indexOf(portrait)
    assert tall.stretch(index) == 1
    assert all(tall.stretch(i) == 0 for i in range(tall.count()) if i != index)


def test_the_robot_stands_on_the_floor_of_its_panel(app):
    """Bottom-aligned, not centred: a robot floating in the middle of a gap
    looks like a mistake, one standing on the bottom edge looks placed."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QColor, QPixmap, QRegion
    from PyQt6.QtWidgets import QWidget
    from deployer.gui import robot_art

    portrait = robot_art.Portrait()
    portrait.resize(300, 460)

    canvas = QPixmap(300, 460)
    canvas.fill(QColor('white'))
    # Children only: with the window background drawn, every pixel differs
    # from white and this measures the widget rather than the robot.
    portrait.render(canvas, QPoint(), QRegion(),
                    QWidget.RenderFlag.DrawChildren)

    image = canvas.toImage()
    painted = [y for y in range(image.height())
               if any(image.pixelColor(x, y) != QColor('white')
                      for x in range(0, image.width(), 3))]
    assert painted, '什么都没画出来'
    assert painted[-1] >= image.height() - 4, '机器人应该站在面板底部'
    assert painted[0] > 0, '上面应该留白,不然就不是按比例缩的'
    portrait.deleteLater()


def test_saving_asks_for_a_name_and_nothing_else(app, monkeypatch, tmp_path):
    """Not a save dialog. A customer handed a directory tree, a file name, an
    extension and a filter puts the list in Downloads and never finds it."""
    asked = {}

    def fake_input(parent, title, label, **kwargs):
        asked['title'] = title
        asked['label'] = label
        asked['prefilled'] = kwargs.get('text')
        return 'KL Gateway Mall', True

    monkeypatch.setattr('PyQt6.QtWidgets.QInputDialog.getText', fake_input)
    chose_a_path = []
    monkeypatch.setattr('PyQt6.QtWidgets.QFileDialog.getSaveFileName',
                        lambda *a, **k: chose_a_path.append(a) or ('', ''))

    window = Deployer(watch_robot=False)
    try:
        window.venue.setText('KL Gateway Mall')
        window._set_rows(GOOD)
        window._export()

        assert asked, '应该问名字'
        assert str(library.directory()) in asked['label'], '要先说清楚存在哪'
        assert asked['prefilled'] == 'KL Gateway Mall', '场地名可以直接用'
        assert chose_a_path == [], '不该让客户挑路径'

        written = library.path_for('KL Gateway Mall')
        assert written.is_file(), '应该存到工具自己的目录'
        assert P.load(written) == GOOD
    finally:
        window.deleteLater()


def test_a_name_that_would_escape_the_folder_is_refused_and_asked_again(
        app, monkeypatch, tmp_path):
    """The one that matters: the name is the only thing between a customer
    and a file somewhere unintended."""
    names = iter([('../../gotcha', True), ('Mall', True)])
    monkeypatch.setattr('PyQt6.QtWidgets.QInputDialog.getText',
                        lambda *a, **k: next(names))
    warned = []
    monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.warning',
                        lambda *a, **k: warned.append(a[2]))

    window = Deployer(watch_robot=False)
    try:
        window._set_rows(GOOD)
        window._export()

        assert warned, '坏名字要说清楚'
        assert not (tmp_path.parent / 'gotcha.yaml').exists()
        assert library.path_for('Mall').is_file(), '改好之后要能存下'
    finally:
        window.deleteLater()


def test_saving_over_an_existing_name_asks_first(app, monkeypatch):
    monkeypatch.setattr('PyQt6.QtWidgets.QInputDialog.getText',
                        lambda *a, **k: ('Mall', True))
    existing = library.path_for('Mall')
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('phrases:\n  - "the old one"\n', encoding='utf-8')

    asked = []
    monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.exec',
                        lambda box: asked.append(box.text()))
    monkeypatch.setattr('PyQt6.QtWidgets.QMessageBox.clickedButton',
                        lambda _s: None)          # neither button: not "yes"

    window = Deployer(watch_robot=False)
    try:
        window._set_rows(GOOD)
        window._export()

        assert asked, '覆盖前要问'
        assert P.load(existing) == ['the old one'], '没点确认就不能覆盖'
    finally:
        window.deleteLater()


def test_the_window_reopens_the_greetings_used_last(app, monkeypatch):
    """A customer with forty curated greetings should not re-import them every
    time the window opens."""
    saved = library.path_for('Mall')
    saved.parent.mkdir(parents=True, exist_ok=True)
    P.save(saved, GOOD)
    library.remember(saved)

    window = Deployer(watch_robot=False)
    try:
        assert window._rows() == GOOD
        assert 'Mall' in log_text(window)
    finally:
        window.deleteLater()


def test_a_remembered_file_that_has_gone_bad_does_not_stop_the_window(
        app, monkeypatch):
    """Files get edited and deleted between sessions. Neither is a reason to
    greet the customer with a dialog before they have done anything."""
    broken = library.path_for('Broken')
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('this: is not a greeting file\n', encoding='utf-8')
    library.remember(broken)

    window = Deployer(watch_robot=False)
    try:
        assert window._rows() == [], '打不开就当没有,表格空着'
        assert window.deploy_button.isEnabled(), '空表格照样可以部署通用问候语'
    finally:
        window.deleteLater()


def test_the_open_dialog_does_not_start_inside_the_packaged_program(
        app, monkeypatch):
    """Frozen, PACKAGE_ROOT is the temporary directory the executable unpacked
    itself into. It holds greeter.yaml and two phrases*.yaml that belong to the
    program, look exactly like greeting lists to choose from, and cease to
    exist when the window closes."""
    from deployer.gui import app as app_module

    started = {}
    monkeypatch.setattr('PyQt6.QtWidgets.QFileDialog.getOpenFileName',
                        lambda _p, _t, where, _f: started.setdefault('where', where) and ('', ''))

    window = Deployer(watch_robot=False)
    try:
        window._import()
        assert started['where'] == str(Path.home())
        assert str(app_module.PACKAGE_ROOT) not in started['where']
    finally:
        window.deleteLater()


def test_opening_a_file_remembers_it_for_next_time(app, monkeypatch, tmp_path):
    elsewhere = tmp_path / 'from-a-usb-stick.yaml'
    P.save(elsewhere, GOOD)
    monkeypatch.setattr('PyQt6.QtWidgets.QFileDialog.getOpenFileName',
                        lambda *a, **k: (str(elsewhere), ''))

    window = Deployer(watch_robot=False)
    try:
        window._import()
        assert window._rows() == GOOD
        assert library.last() == elsewhere.resolve()
    finally:
        window.deleteLater()
