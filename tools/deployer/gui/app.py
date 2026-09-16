"""The X2 deployer window.

Written for someone who has never opened a terminal. Three rules follow.

Nothing is asked for that can be found out. The robot's address, the password,
the detector weights, the package to deploy: each has a working default, and
the window says what it found rather than asking the customer to supply it.

Nothing runs on the UI thread. A deployment takes minutes and spends most of
that waiting on a robot, so it runs in a worker and arrives back as signals.
A frozen window is indistinguishable from a crashed one.

Nothing claims success early. The deploy button does not go green when systemd
reports the service active -- it goes green when the robot's own startup line
comes back saying which camera and which backend are in force. Every failure
this project has produced looked healthy at every earlier point.
"""
from __future__ import annotations

import datetime
import sys
import threading
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deployer.core import (assets, library, logbook,                 # noqa: E402
                           phrases as P, watch)
from deployer.gui import robot_art                                  # noqa: E402
from deployer.core.i18n import (LANGUAGES, language, set_language, t,  # noqa: E402
                                use_system_language)
from deployer.core.deployment import (Deployment, Event, Plan,       # noqa: E402
                                      Status, Uninstall)
from deployer.core.robot import (                                   # noqa: E402
    DEFAULT_HOST, DEFAULT_PASSWORD, DEFAULT_USER, Robot, RobotError)

def _package_root() -> Path:
    """What to upload to the robot.

    Frozen, that is payload/ inside the unpacked bundle -- deliberately not
    the bundle itself, which also holds the Python runtime this program is
    running on. From source, it is the repository.
    """
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        return Path(bundle) / 'payload'
    return Path(__file__).resolve().parents[2].parent


PACKAGE_ROOT = _package_root()

INK = '#1d252b'
ACCENT = '#0f6d78'
OK_GREEN = '#2c6a45'
FAIL_RED = '#a6332a'
AMBER = '#9c6b1a'
WAIT_GREY = '#5d686f'


# ---------------------------------------------------------------- workers

class WatchWorker(QObject):
    """Looks for the robot every couple of seconds, for as long as the window
    is open.

    A "Find robot" button asks the customer to guess when the answer might
    have changed: they plug the cable in, press it, are told the address is
    wrong, fix the address -- and have to remember to press it again. Each of
    those is a place to give up. Polling removes all of them.

    `done` exists only so _start() can stop the thread the same way it stops
    every other worker; this one is finished when it is told to be.
    """

    status = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, watcher: watch.Watcher):
        super().__init__()
        self._watcher = watcher
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self.status.emit(self._watcher.poll())
            except Exception as exc:        # noqa: BLE001 - must never die
                logbook.write_exception('watch', exc)
            # Interruptible: closing the window should not wait two seconds.
            self._stop.wait(watch.POLL_S)
        self.done.emit(None)


class DownloadWorker(QObject):
    progress = pyqtSignal(str, int)
    done = pyqtSignal(str, str)             # directory, error

    def run(self):
        try:
            self.done.emit(assets.download(self.progress.emit), '')
        except Exception as exc:            # noqa: BLE001
            self.done.emit('', str(exc))


class UninstallWorker(QObject):
    reported = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, robot: Robot):
        super().__init__()
        self._robot = robot

    def run(self):
        outcome = Uninstall(self._robot, self.reported.emit).run()
        try:
            self._robot.close()
        except Exception:                   # noqa: BLE001 - already finished
            pass
        self.done.emit(outcome)


class DeployWorker(QObject):
    # Not `event`: QObject.event() is the virtual Qt calls to dispatch events
    # to an object, and a signal of that name shadows it. moveToThread then
    # raised "native Qt signal is not callable" from inside Qt, left the
    # thread half-moved, and the process aborted with "QThread: Destroyed
    # while thread is still running" -- which the customer saw as the window
    # vanishing the moment they pressed Deploy.
    reported = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, robot: Robot, plan: Plan):
        super().__init__()
        self._robot, self._plan = robot, plan

    def run(self):
        outcome = Deployment(self._robot, self._plan, self.reported.emit).run()
        try:
            self._robot.close()
        except Exception:                   # noqa: BLE001 - already finished
            pass
        self.done.emit(outcome)


# ---------------------------------------------------------------- window

class Deployer(QWidget):
    def __init__(self, watch_robot: bool = True):
        """watch_robot=False builds the window without its polling thread.

        Tests build a lot of these, and a QThread whose owner is collected
        before it is stopped aborts the process -- which is how the suite
        started dying at signal 6 the moment polling was added.
        """
        super().__init__()
        self.setWindowTitle(t('app.title'))
        self.resize(1060, 740)
        self._thread: Optional[QThread] = None
        self._weights_dir: Optional[str] = None

        # Two columns, because only one thing on this window grows: the
        # greeting list. Stacked vertically it had to share height with a
        # one-line status and a handful of controls, and the customer scrolled
        # a table to read their own sentences. Now the narrow column holds
        # everything that is a fixed size and the wide one is all list.
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(self._header_row())

        columns = QHBoxLayout()
        columns.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._masthead())
        left.addWidget(self._robot_box())
        left.addWidget(self._deploy_box())
        # The picture takes the slack instead of an invisible spacer, so the
        # two columns end level and the left one stops looking half-finished.
        self.portrait = robot_art.Portrait()
        left.addWidget(self.portrait, stretch=1)

        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(360)
        columns.addWidget(holder)
        columns.addWidget(self._phrases_box(), stretch=1)
        layout.addLayout(columns, stretch=3)

        layout.addWidget(self._log_box(), stretch=2)

        # Across the bottom, under the log it summarises. In the narrow column
        # it was a short bar in a panel of its own, easy to miss and easy to
        # mistake for something broken while it sat empty.
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat(t('deploy.idle'))
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        layout.addWidget(self.progress)

        self._fix_text = ''
        self._robot_ready = False
        # Set before _find_weights(), which decides the deploy button with it.
        self._phrases_ok = False
        # While a deployment runs, the watcher keeps polling but says nothing:
        # its lines every ten seconds would interleave with the steps the
        # customer is actually watching, and the lamp belongs to the outcome.
        self._deploying = False
        self._watching = watch_robot
        # Owned by the window, not by the thread: _on_status is a slot and can
        # be driven without a watcher running behind it.
        self._throttle = watch.Throttle()
        self._find_weights()
        self._revalidate()
        self._reopen_last()
        if watch_robot:
            self._start_watching()

    # -- sections ---------------------------------------------------------

    def _masthead(self) -> QWidget:
        """What this program is, in two lines, above the numbered steps.

        The picture that used to sit beside these words is now at the foot of
        the column where it has room to be a picture. This is only the name.
        """
        card = QWidget()
        words = QVBoxLayout(card)
        words.setContentsMargins(4, 0, 4, 0)
        words.setSpacing(2)

        title = QLabel(t('app.title'))
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel(t('app.tagline'))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f'color: {WAIT_GREY};')

        words.addWidget(title)
        words.addWidget(subtitle)
        return card

    def _header_row(self) -> QHBoxLayout:
        """Language, and a menu for everything that is not one of the steps.

        Uninstall lived at the bottom of the deploy panel, inside the numbered
        1-2-3 flow, where it read as a fourth step. Flattening the button did
        not help: the problem was where it was, not how loud. A menu says
        "this is maintenance" without a word of explanation.
        """
        row = QHBoxLayout()
        row.addStretch(1)

        self.more_button = QPushButton('⋯')
        self.more_button.setFlat(True)
        self.more_button.setFixedWidth(34)
        self.more_button.setToolTip(t('app.more'))
        menu = QMenu(self)
        self.uninstall_action = menu.addAction(t('uninstall.button'))
        self.uninstall_action.triggered.connect(self._uninstall)
        # Nothing to remove until a robot is answering, and offering it then
        # would only produce a failure the customer has to interpret.
        self.uninstall_action.setEnabled(False)
        self.more_button.setMenu(menu)
        row.addWidget(self.more_button)

        self.language_button = QPushButton(t('app.language'))
        self.language_button.setFlat(True)
        self.language_button.setMaximumWidth(110)
        self.language_button.clicked.connect(self._switch_language)
        row.addWidget(self.language_button)
        return row

    def _switch_language(self) -> None:
        """Swap languages by building a fresh window.

        Qt cannot retranslate text that was set from a plain string, and
        walking every widget to reassign it is how half a window ends up in
        one language and half in the other. The greetings, the venue and the
        log are carried across, because losing a customer's typing to a
        button they pressed out of curiosity would be unforgivable.
        """
        other = LANGUAGES[(LANGUAGES.index(language()) + 1) % len(LANGUAGES)]
        carried = (self._rows(), self.venue.text(),
                   [self.log.item(i).text() for i in range(self.log.count())],
                   self.identity.text())
        set_language(other)

        # The new window watches if this one did: a language switch is not a
        # reason to stop looking for the robot, nor to start.
        fresh = Deployer(watch_robot=self._watching)
        fresh._set_rows(carried[0])
        fresh.venue.setText(carried[1])
        for line in carried[2]:
            fresh.log.addItem(line)
        fresh.identity.setText(carried[3])
        fresh.resize(self.size())
        fresh.move(self.pos())
        fresh.show()

        # Held on the application so it is not collected the moment this
        # window closes and drops the last reference to it.
        QApplication.instance()._deployer_window = fresh
        self.close()          # closeEvent stops this window's polling thread


    def _robot_box(self) -> QGroupBox:
        """No address, user or password to fill in.

        Every X2 answers on the same address with the same vendor account, so
        asking would be offering three ways to get it wrong and no way to get
        it more right. They are shown, not editable: a customer who cannot
        connect needs to know what was tried, and telling them to check the
        cable is more use than a text box.
        """
        box = QGroupBox(t('section.robot'))
        outer = QVBoxLayout(box)

        # One row: a dot, a state, and the way out of it. The explanation --
        # which address this computer has, and which panel to change it in --
        # used to be the label, and wrapped to six lines that pushed the panel
        # out of shape. It goes to the progress log and the fix dialog, where
        # there is room for it.
        line = QHBoxLayout()
        self.lamp = QLabel('●')
        self.lamp.setStyleSheet(f'color: {WAIT_GREY}; font-size: 20px;')
        self.identity = QLabel(t('watch.label_no_link'))
        self.identity.setStyleSheet(f'color: {WAIT_GREY};')
        self.fix_button = QPushButton(t('watch.how_to_fix'))
        self.fix_button.clicked.connect(self._show_fix)
        self.fix_button.hide()
        line.addWidget(self.lamp)
        line.addWidget(self.identity, stretch=1)
        line.addWidget(self.fix_button)
        outer.addLayout(line)

        target = QLabel(t('robot.target', host=DEFAULT_HOST, user=DEFAULT_USER))
        target.setStyleSheet(f'color: {WAIT_GREY}; font-size: 11px;')
        outer.addWidget(target)
        return box

    def _show_fix(self) -> None:
        """The full instructions, on demand.

        They run to eight lines and name a settings panel; parking that in the
        window would push everything else off the screen, and it is only
        needed by the customer who is stuck."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(t('watch.fix_title'))
        box.setText(self._fix_text or '')
        box.exec()

    def _phrases_box(self) -> QGroupBox:
        box = QGroupBox(t('section.phrases'))
        outer = QVBoxLayout(box)

        top = QHBoxLayout()
        self.venue = QLineEdit()
        self.venue.setPlaceholderText(t('phrases.venue_hint'))
        top.addWidget(QLabel(t('phrases.venue')))
        top.addWidget(self.venue, stretch=1)
        outer.addLayout(top)

        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels([t('phrases.column')])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(26)
        # Rows grow to fit the sentence. A greeting clipped at the right edge
        # is the one thing on this window the customer is actually here to
        # read, and they were scrolling sideways to finish their own wording.
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(lambda _item: self._revalidate())
        outer.addWidget(self.table, stretch=1)

        buttons = QHBoxLayout()
        for key, slot in (('phrases.add', self._add_row),
                          ('phrases.delete', self._delete_rows),
                          ('phrases.open', self._import),
                          ('phrases.fix', self._autofix),
                          ('phrases.save', self._export)):
            button = QPushButton(t(key))
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        outer.addWidget(self.problems)
        return box

    def _deploy_box(self) -> QGroupBox:
        box = QGroupBox(t('section.deploy'))
        outer = QVBoxLayout(box)

        # A state on its own line, and the button only when it is the answer.
        # The path used to be in the label, and inside the packaged program
        # that path is the temporary directory it unpacked itself into --
        # meaningless to a customer and long enough to break the panel.
        self.weights_label = QLabel()
        outer.addWidget(self.weights_label)

        self.download_button = QPushButton(t('weights.download'))
        self.download_button.clicked.connect(self._download)
        self.download_button.hide()
        outer.addWidget(self.download_button)

        # The choice lives on the button rather than in a checkbox above it.
        # "Start automatically when the robot boots" asked the customer to
        # decide something before they knew it was being asked; two sentences
        # at the moment of pressing say what each one leaves behind.
        self.deploy_button = QPushButton(t('deploy.start'))
        self.deploy_button.setMinimumHeight(44)
        font = QFont(self.deploy_button.font())
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.deploy_button.setFont(font)
        menu = QMenu(self)
        self.autostart_action = menu.addAction(t('deploy.permanent'))
        self.autostart_action.setToolTip(t('deploy.permanent_why'))
        self.autostart_action.triggered.connect(lambda: self._deploy(True))
        self.once_action = menu.addAction(t('deploy.once'))
        self.once_action.setToolTip(t('deploy.once_why'))
        self.once_action.triggered.connect(lambda: self._deploy(False))
        # Qt hides action tooltips in menus unless asked; the label is short
        # on purpose and the consequence has to be reachable.
        menu.setToolTipsVisible(True)
        self.deploy_button.setMenu(menu)
        outer.addWidget(self.deploy_button)

        self.result = QLabel()
        self.result.setWordWrap(True)
        outer.addWidget(self.result)
        return box

    def _log_box(self) -> QGroupBox:
        box = QGroupBox(t('section.log'))
        outer = QVBoxLayout(box)
        # A list, not a text box. Entries arrive one at a time and are read
        # one at a time; in a text box a multi-line failure ran together with
        # whatever came next and the customer could not tell where one thing
        # ended. One row per entry, each stamped, each coloured by what it is.
        self.log = QListWidget()
        self.log.setAlternatingRowColors(True)
        self.log.setFont(QFont('monospace', 9))
        self.log.setWordWrap(False)
        outer.addWidget(self.log)

        # The panel above is lost when the program dies, which is exactly when
        # somebody needs it. The file is not, so the window says where it is.
        row = QHBoxLayout()
        where = QLabel(t('log.saved_to', path=logbook.path() or logbook.directory()))
        where.setStyleSheet(f'color: {WAIT_GREY}; font-size: 11px;')
        where.setWordWrap(True)
        open_button = QPushButton(t('log.open'))
        open_button.clicked.connect(self._open_log_folder)
        row.addWidget(where, stretch=1)
        row.addWidget(open_button)
        outer.addLayout(row)
        return box

    def _open_log_folder(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logbook.directory())))

    # -- phrase table -----------------------------------------------------

    def _rows(self) -> List[str]:
        return [(self.table.item(r, 0).text() if self.table.item(r, 0) else '')
                for r in range(self.table.rowCount())]

    def _set_rows(self, items: List[str]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        for row, text in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(text))
        self.table.blockSignals(False)
        self._revalidate()

    def _add_row(self) -> None:
        self.table.insertRow(self.table.rowCount())
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0)
                            or QTableWidgetItem())

    def _delete_rows(self) -> None:
        for index in sorted({i.row() for i in self.table.selectedIndexes()},
                            reverse=True):
            self.table.removeRow(index)
        self._revalidate()

    def _import(self) -> None:
        # Starts beside the file used last, never inside this program. Frozen,
        # that is the temporary directory the executable unpacked itself into:
        # it holds greeter.yaml and two phrases*.yaml belonging to the program,
        # which read as "the greeting lists you can pick from" and vanish when
        # the window closes. Filtered, because anything else a customer might
        # open is a YAML with no greetings in it and phrases.load() names it
        # rather than talking about sections.
        path, _ = QFileDialog.getOpenFileName(
            self, t('phrases.open_title'),
            str(library.start_directory()), t('phrases.filter'))
        if not path:
            return
        try:
            self._set_rows(P.load(path))
        except Exception as exc:                         # noqa: BLE001
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(t('phrases.not_greetings'))
            box.setText(str(exc))
            box.exec()
            self._say(t('phrases.open_failed', error=exc))
            return
        library.remember(path)
        self._say(t('phrases.opened', count=self.table.rowCount(), path=path))

    def _export(self) -> None:
        """Ask for a name, not for a place.

        A save dialog hands somebody who has never opened a terminal a
        directory tree, a file name, an extension and a filter, and the usual
        result is a greeting list in Downloads under a name nobody recognises
        a month later. The file goes where everything else this program writes
        goes, and the window says where that is.
        """
        suggestion = self.venue.text().strip()
        previous = library.last()
        if not suggestion and previous is not None:
            suggestion = previous.stem

        while True:
            # The label says where it will land, which is the question the
            # customer would otherwise ask ten seconds after pressing Save.
            name, chose = QInputDialog.getText(
                self, t('phrases.save_title'),
                t('phrases.save_prompt') + '\n'
                + t('phrases.save_where', path=library.directory()),
                text=suggestion)
            if not chose:
                return
            try:
                target = library.path_for(name)
                break
            except ValueError as exc:
                # Asked again with what they typed still in the box: the name
                # is nearly right, and retyping it is the annoying part.
                QMessageBox.warning(self, t('phrases.bad_name'), str(exc))
                suggestion = name

        if target.exists() and not self._confirm_overwrite(target):
            return

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            P.save(target, P.apply_fixes(self._rows()),
                   venue=self.venue.text().strip())
        except OSError as exc:
            QMessageBox.warning(self, t('phrases.bad_name'),
                                t('phrases.save_failed', error=exc))
            return

        library.remember(target)
        self._say(t('phrases.saved', path=target))

    def _confirm_overwrite(self, target) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(t('phrases.overwrite_title'))
        box.setText(t('phrases.overwrite_body', name=target.stem))
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.exec()
        return box.clickedButton() is box.button(QMessageBox.StandardButton.Yes)

    def _reopen_last(self) -> None:
        """Put back the greetings the customer was working on.

        Without this a customer with a curated list of forty re-imports it
        every time the window opens, which is the sort of friction that ends
        with them deploying the standard greetings by accident.
        """
        previous = library.last()
        if previous is None:
            return
        try:
            rows = P.load(previous)
        except Exception as exc:                         # noqa: BLE001
            # A file edited into nonsense since, or gone unreadable. Not worth
            # a dialog before the customer has done anything: the table simply
            # starts empty, as it did before.
            logbook.write(f'could not reopen {previous}: {exc}')
            return
        if not rows:
            return
        self._set_rows(rows)
        self._say(t('phrases.reopened', count=len(rows), name=previous.stem))

    def _autofix(self) -> None:
        before = self._rows()
        self._set_rows(P.apply_fixes(before))
        self._say(t('phrases.fixed', before=len(before), after=self.table.rowCount()))

    def _revalidate(self) -> None:
        rows = self._rows()
        problems = P.check(rows)
        by_line = {p.line for p in problems}

        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            bad = (row + 1) in by_line
            item.setBackground(QColor('#f7ece2') if bad else QColor(0, 0, 0, 0))
        self.table.blockSignals(False)

        if not [r for r in rows if r.strip()]:
            # Empty is allowed, and means the standard greetings. It is not a
            # mistake to be corrected -- it is the default, and the deploy
            # button says so before acting on it.
            self.problems.setText(t('phrases.will_use_standard'))
            self.problems.setStyleSheet(f'color: {WAIT_GREY};')
            self._phrases_ok = True
            self._refresh_deploy_button()
            return

        if not problems:
            count = len([r for r in rows if r.strip()])
            self.problems.setText(t('phrases.all_good', count=count))
            self.problems.setStyleSheet(f'color: {OK_GREEN};')
        else:
            fixable = sum(1 for p in problems if p.fixable)
            head = '；'.join(str(p) for p in problems[:3])
            more = (t('phrases.more', count=len(problems) - 3)
                    if len(problems) > 3 else '')
            tip = t('phrases.can_fix') if fixable else ''
            self.problems.setText(f'{head}{more}{tip}')
            self.problems.setStyleSheet(f'color: {FAIL_RED};')

        self._phrases_ok = not any(not p.fixable for p in problems)
        self._refresh_deploy_button()

    def _refresh_deploy_button(self) -> None:
        """The only place the deploy button is turned on or off.

        Two unrelated things can stop a deployment and either on its own is
        enough: a greeting the robot cannot say, and a missing vision model.
        Deciding it in one place is what stops a third condition, later, from
        leaving a stale True behind in whichever branch forgot about it.

        Without the model the robot still starts and still looks healthy -- it
        just falls back to a detector that walks past people. That is the worst
        kind of failure this project has, so it stops the deployment rather
        than warning about it.
        """
        self.deploy_button.setEnabled(self._phrases_ok and bool(self._weights_dir))
        # Only when the model is the one thing in the way: a grey button whose
        # reason is about greetings already has that reason above the table.
        self.deploy_button.setToolTip(
            t('weights.blocks_deploy')
            if self._phrases_ok and not self._weights_dir else '')

    # -- weights ----------------------------------------------------------

    def _find_weights(self) -> None:
        found = assets.locate()
        self._weights_dir = found.directory
        if found.ready:
            self.weights_label.setText(t('weights.state_ready'))
            self.weights_label.setStyleSheet(f'color: {OK_GREEN};')
            self.weights_label.setToolTip(found.directory or '')
            self.download_button.hide()
        else:
            self.weights_label.setText(t('weights.state_missing'))
            self.weights_label.setStyleSheet(f'color: {FAIL_RED};')
            self.weights_label.setToolTip(t('weights.why'))
            self.download_button.setEnabled(True)
            self.download_button.show()
        self._refresh_deploy_button()

    def _download(self) -> None:
        self.download_button.setEnabled(False)
        worker = DownloadWorker()
        def downloading(name: str, percent: int) -> None:
            self.progress.setFormat(
                t('weights.downloading', name=name, percent=percent))
            self.progress.setValue(percent)

        worker.progress.connect(downloading)

        def finished(directory: str, error: str) -> None:
            if error:
                self._say(t('weights.download_failed', error=error))
                QMessageBox.warning(self, t('weights.download_failed_title'), error)
                self.download_button.setEnabled(True)
            else:
                self._say(t('weights.downloaded', path=directory))
            self._find_weights()
            self.progress.setValue(0)
            self.progress.setFormat(t('deploy.idle'))

        worker.done.connect(finished)
        self._start(worker)

    # -- robot ------------------------------------------------------------

    def _start_watching(self) -> None:
        self._watch_worker = WatchWorker(watch.Watcher(
            DEFAULT_HOST, DEFAULT_USER, DEFAULT_PASSWORD))
        self._watch_worker.status.connect(self._on_status)

        thread = QThread(self)
        self._watch_worker.moveToThread(thread)
        thread.started.connect(self._watch_worker.run)
        self._watch_worker.done.connect(thread.quit)
        self._watch_thread = thread
        thread.start()

    def _on_status(self, status) -> None:
        self._robot_ready = status.ready
        self.uninstall_action.setEnabled(status.ready and not self._deploying)
        if self._deploying:
            return

        colours = {watch.NO_LINK: WAIT_GREY, watch.WRONG_SUBNET: FAIL_RED,
                   watch.NO_SSH: AMBER, watch.READY: OK_GREEN}
        colour = colours.get(status.state, WAIT_GREY)
        self.lamp.setStyleSheet(f'color: {colour}; font-size: 20px;')

        labels = {watch.NO_LINK: 'watch.label_no_link',
                  watch.WRONG_SUBNET: 'watch.label_wrong_subnet',
                  watch.NO_SSH: 'watch.label_no_ssh',
                  watch.READY: 'watch.label_ready'}
        self.identity.setText(t(labels.get(status.state, 'watch.label_no_link')))
        self.identity.setStyleSheet(f'color: {colour};')
        self.identity.setToolTip(str(status.identity or status.detail))

        self._fix_text = status.detail
        self.fix_button.setVisible(not status.ready)

        if self._throttle.should_log(status):
            kinds = {watch.READY: 'ok', watch.WRONG_SUBNET: 'fail',
                     watch.NO_SSH: 'warn'}
            self._say(status.detail, kinds.get(status.state, 'info'))

    def stop_watching(self) -> None:
        """Stop the polling thread and wait for it. Safe to call twice."""
        worker = getattr(self, '_watch_worker', None)
        thread = getattr(self, '_watch_thread', None)
        if worker is not None:
            worker.stop()
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(3000)

    def closeEvent(self, event):                        # noqa: N802 - Qt name
        """Stop the watcher before the window goes.

        A QThread still running when its last reference goes away aborts the
        process, which is what the customer would see as the program crashing
        on exit."""
        self.stop_watching()
        super().closeEvent(event)

    # -- deploying --------------------------------------------------------

    def _deploy(self, install_service: bool = True) -> None:
        if not self._weights_dir:
            QMessageBox.warning(self, t('weights.needed'), t('weights.needed_body'))
            return

        phrases = P.apply_fixes(self._rows())
        if not phrases:
            phrases = self._confirm_standard_greetings()
            if phrases is None:
                return

        self._deploying = True
        self.lamp.setStyleSheet(f'color: {AMBER}; font-size: 20px;')
        self.identity.setText(t('deploy.running'))
        self.identity.setStyleSheet(f'color: {AMBER};')
        self.fix_button.hide()
        self.deploy_button.setEnabled(False)
        self.result.setText('')
        self.progress.setValue(0)
        self._steps_done = 0
        self._steps_total = 8 if install_service else 7

        robot = Robot(DEFAULT_HOST, DEFAULT_USER, DEFAULT_PASSWORD)
        plan = Plan(package_dir=str(PACKAGE_ROOT),
                    phrases=phrases,
                    weights_dir=self._weights_dir,
                    venue=self.venue.text().strip(),
                    install_service=install_service,
                    start_after=True)

        worker = DeployWorker(robot, plan)
        worker.reported.connect(self._on_event)
        worker.done.connect(self._on_done)
        self._say(t('deploy.begin'))
        self._start(worker)

    def _confirm_standard_greetings(self):
        """Ask before greeting a customer's visitors in words they never saw.

        The standard list is a fallback, not a draft: pre-filling the table
        with it would let someone edit it, deploy, and believe the result was
        their own writing. Leaving the table empty and asking here keeps the
        two apart -- and the question names three of the lines, because
        "standard greetings" tells nobody what the robot will actually say.

        Returns the greetings to deploy, or None if the customer would rather
        write their own first.
        """
        standard = P.defaults()
        if not standard:
            QMessageBox.warning(self, t('phrases.none_title'),
                                t('phrases.none_body'))
            return None

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(t('phrases.standard_title'))
        box.setText(t('phrases.standard_body', count=len(standard)))
        box.setInformativeText('\n'.join(f'· {line}' for line in standard[:3])
                               + '\n· …')
        use = box.addButton(t('phrases.standard_use'),
                            QMessageBox.ButtonRole.AcceptRole)
        box.addButton(t('phrases.standard_write'),
                      QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() is not use:
            return None
        self._say(t('phrases.standard_chosen', count=len(standard)))
        return standard

    def _uninstall(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t('uninstall.confirm_title'))
        box.setText(t('uninstall.confirm_body'))
        remove = box.addButton(t('uninstall.confirm_yes'),
                               QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(t('uninstall.confirm_no'), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not remove:
            return

        self._deploying = True
        self.deploy_button.setEnabled(False)
        self.result.setText('')
        self.progress.setValue(0)
        self._steps_done, self._steps_total = 0, 5

        worker = UninstallWorker(Robot(DEFAULT_HOST, DEFAULT_USER, DEFAULT_PASSWORD))
        worker.reported.connect(self._on_event)
        worker.done.connect(self._on_uninstalled)
        self._say(t('uninstall.button'))
        self._start(worker)

    def _on_uninstalled(self, outcome) -> None:
        self._deploying = False
        self.deploy_button.setEnabled(True)
        ok = bool(getattr(outcome, 'ok', False))
        self.progress.setValue(100 if ok else self.progress.value())
        self.progress.setFormat(t('deploy.done') if ok else t('deploy.failed'))
        self.result.setText(t('uninstall.done') if ok else t('uninstall.failed'))
        self.result.setStyleSheet(f'color: {OK_GREEN if ok else FAIL_RED};')

    def _on_event(self, event: Event) -> None:
        logbook.write(f'[{event.status.value}] {event.step}'
                      + (f' -- {event.message}' if event.message else ''))
        if event.status is Status.RUNNING:
            self.progress.setFormat(f'{event.step}…')
            self._say(f'{event.step}…')
        elif event.status is Status.OK:
            self._steps_done += 1
            self.progress.setValue(
                int(self._steps_done * 100 / max(self._steps_total, 1)))
            self._say(f'✓ {event.step}'
                      + (f' — {event.message}' if event.message else ''), 'ok')
        elif event.status is Status.INFO:
            self._say(f'  {event.message}')
        else:
            self._say(f'✗ {event.step} — {event.message}', 'fail')

    def _on_done(self, outcome) -> None:
        self._deploying = False
        self.deploy_button.setEnabled(True)
        colour = OK_GREEN if outcome.ok else FAIL_RED
        self.lamp.setStyleSheet(f'color: {colour}; font-size: 20px;')
        self.identity.setText(t('deploy.lamp_ok' if outcome.ok else 'deploy.lamp_failed'))
        self.identity.setStyleSheet(f'color: {colour};')
        if outcome.ok:
            self.progress.setValue(100)
            self.progress.setFormat(t('deploy.done'))
            self.result.setText(t('deploy.success', identity=outcome.identity,
                                  startup=outcome.startup_line))
            self.result.setStyleSheet(f'color: {OK_GREEN};')
            self._say(t('deploy.finished'))
        else:
            self.progress.setFormat(t('deploy.failed'))
            self.result.setText(t('deploy.failure'))
            self.result.setStyleSheet(f'color: {FAIL_RED};')
            self._say(t('deploy.unfinished'))

    # -- plumbing ---------------------------------------------------------

    LOG_COLOURS = {'ok': OK_GREEN, 'fail': FAIL_RED, 'warn': AMBER,
                   'info': INK}

    def _say(self, text: str, kind: str = 'info') -> None:
        """One line in the panel, and the whole thing in the file.

        The panel gets the first line only: a failure's instructions run to
        eight lines and would push the deployment's own progress off the
        screen. The file keeps all of it, which is what gets sent to support.
        """
        logbook.write(text)

        first = text.splitlines()[0] if text else ''
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        item = QListWidgetItem(f'{stamp}   {first}')
        item.setForeground(QColor(self.LOG_COLOURS.get(kind, INK)))
        if len(text.splitlines()) > 1:
            item.setToolTip(text)          # the rest, on hover
        self.log.addItem(item)
        self.log.scrollToBottom()

    def _start(self, worker: QObject) -> None:
        """Run one worker on its own thread, keeping both alive until it ends.

        Qt deletes a QThread whose last Python reference goes away, taking the
        running worker with it, so both are held on self until finished fires.
        """
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()


def main() -> int:
    logbook.start()
    app = QApplication(sys.argv)
    # Chinese for a Chinese system, English for anything else, before the
    # window is built -- every label is read once, at construction.
    use_system_language()
    app.setApplicationName(t('app.title'))
    window = Deployer()
    # Maximised, not true fullscreen: fullscreen takes the title bar with it,
    # and a customer who cannot find the close button is worse off than one
    # with a small window.
    window.showMaximized()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
