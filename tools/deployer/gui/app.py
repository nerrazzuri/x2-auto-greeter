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

import sys
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deployer.core import assets, phrases as P                      # noqa: E402
from deployer.core.deployment import Deployment, Event, Plan, Status  # noqa: E402
from deployer.core.robot import (                                   # noqa: E402
    DEFAULT_HOST, DEFAULT_PASSWORD, DEFAULT_USER, Robot, RobotError)

PACKAGE_ROOT = Path(__file__).resolve().parents[2].parent   # the repository

OK_GREEN = '#2c6a45'
FAIL_RED = '#a6332a'
WAIT_GREY = '#5d686f'


# ---------------------------------------------------------------- workers

class DetectWorker(QObject):
    done = pyqtSignal(object, str)          # Identity | None, error

    def __init__(self, host, user, password):
        super().__init__()
        self._args = (host, user, password)

    def run(self):
        try:
            with Robot(*self._args) as robot:
                self.done.emit(robot.identify(), '')
        except RobotError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:            # noqa: BLE001
            self.done.emit(None, f'{type(exc).__name__}: {exc}')


class DownloadWorker(QObject):
    progress = pyqtSignal(str, int)
    done = pyqtSignal(str, str)             # directory, error

    def run(self):
        try:
            self.done.emit(assets.download(self.progress.emit), '')
        except Exception as exc:            # noqa: BLE001
            self.done.emit('', str(exc))


class DeployWorker(QObject):
    event = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, robot: Robot, plan: Plan):
        super().__init__()
        self._robot, self._plan = robot, plan

    def run(self):
        outcome = Deployment(self._robot, self._plan, self.event.emit).run()
        try:
            self._robot.close()
        except Exception:                   # noqa: BLE001 - already finished
            pass
        self.done.emit(outcome)


# ---------------------------------------------------------------- window

class Deployer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('X2 迎宾部署')
        self.resize(820, 900)
        self._thread: Optional[QThread] = None
        self._weights_dir: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(self._robot_box())
        layout.addWidget(self._phrases_box(), stretch=1)
        layout.addWidget(self._deploy_box())
        layout.addWidget(self._log_box(), stretch=1)

        self._find_weights()
        self._revalidate()

    # -- sections ---------------------------------------------------------

    def _robot_box(self) -> QGroupBox:
        """No address, user or password to fill in.

        Every X2 answers on the same address with the same vendor account, so
        asking would be offering three ways to get it wrong and no way to get
        it more right. They are shown, not editable: a customer who cannot
        connect needs to know what was tried, and telling them to check the
        cable is more use than a text box.
        """
        box = QGroupBox('1 · 机器人')
        outer = QVBoxLayout(box)

        line = QHBoxLayout()
        self.detect_button = QPushButton('检测机器人')
        self.detect_button.clicked.connect(self._detect)
        self.identity = QLabel('把网线插到机器人上,然后点「检测机器人」。')
        self.identity.setWordWrap(True)
        self.identity.setStyleSheet(f'color: {WAIT_GREY};')
        line.addWidget(self.detect_button)
        line.addWidget(self.identity, stretch=1)
        outer.addLayout(line)

        target = QLabel(f'连接目标 {DEFAULT_HOST} · 用户 {DEFAULT_USER}')
        target.setStyleSheet(f'color: {WAIT_GREY}; font-size: 11px;')
        outer.addWidget(target)
        return box

    def _phrases_box(self) -> QGroupBox:
        box = QGroupBox('2 · 问候语')
        outer = QVBoxLayout(box)

        top = QHBoxLayout()
        self.venue = QLineEdit()
        self.venue.setPlaceholderText('场地名称,例如:KL Gateway Mall(可留空)')
        top.addWidget(QLabel('场地'))
        top.addWidget(self.venue, stretch=1)
        outer.addLayout(top)

        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(['机器人会说的话(每行一句,随机挑一句)'])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.itemChanged.connect(lambda _item: self._revalidate())
        outer.addWidget(self.table, stretch=1)

        buttons = QHBoxLayout()
        for text, slot in (('加一句', self._add_row),
                           ('删除选中', self._delete_rows),
                           ('打开问候语文件', self._import),
                           ('自动修正', self._autofix),
                           ('保存到文件', self._export)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        outer.addWidget(self.problems)
        return box

    def _deploy_box(self) -> QGroupBox:
        box = QGroupBox('3 · 部署')
        outer = QVBoxLayout(box)

        row = QHBoxLayout()
        self.weights_label = QLabel()
        self.weights_label.setWordWrap(True)
        self.download_button = QPushButton('下载识别模型')
        self.download_button.clicked.connect(self._download)
        row.addWidget(self.weights_label, stretch=1)
        row.addWidget(self.download_button)
        outer.addLayout(row)

        self.autostart = QCheckBox('机器人开机后自动运行(推荐)')
        self.autostart.setChecked(True)
        outer.addWidget(self.autostart)

        self.deploy_button = QPushButton('开始部署')
        self.deploy_button.setMinimumHeight(44)
        font = QFont(self.deploy_button.font())
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.deploy_button.setFont(font)
        self.deploy_button.clicked.connect(self._deploy)
        outer.addWidget(self.deploy_button)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat('尚未开始')
        outer.addWidget(self.progress)

        self.result = QLabel()
        self.result.setWordWrap(True)
        outer.addWidget(self.result)
        return box

    def _log_box(self) -> QGroupBox:
        box = QGroupBox('过程记录')
        outer = QVBoxLayout(box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont('monospace'))
        outer.addWidget(self.log)
        return box

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
        # Opens in the directory the greeting lists live in. The repository
        # also holds tools/deploy/sites/klgw.yaml, which is that deployment's
        # *settings* and not its greetings -- and it is the file a customer
        # reaches for first, because it is the one named after the site.
        start = PACKAGE_ROOT / 'x2_greeter_ws' / 'src' / 'x2_greeter' / 'config'
        path, _ = QFileDialog.getOpenFileName(
            self, '打开问候语文件',
            str(start if start.is_dir() else Path.home()),
            '问候语 (phrases*.yaml *.txt);;所有文件 (*)')
        if not path:
            return
        try:
            self._set_rows(P.load(path))
            self._say(f'已导入 {self.table.rowCount()} 句:{path}')
        except Exception as exc:                         # noqa: BLE001
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle('这个文件不是问候语')
            box.setText(str(exc))
            box.exec()
            self._say(f'打开失败:{exc}')

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, '保存问候语', 'phrases.yaml', '问候语文件 (*.yaml)')
        if not path:
            return
        P.save(path, P.apply_fixes(self._rows()), venue=self.venue.text().strip())
        self._say(f'已保存:{path}')

    def _autofix(self) -> None:
        before = self._rows()
        self._set_rows(P.apply_fixes(before))
        self._say(f'自动修正:{len(before)} 句 → {self.table.rowCount()} 句')

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

        if not problems:
            count = len([r for r in rows if r.strip()])
            self.problems.setText(f'✓ {count} 句,没有问题。')
            self.problems.setStyleSheet(f'color: {OK_GREEN};')
        else:
            fixable = sum(1 for p in problems if p.fixable)
            head = '；'.join(str(p) for p in problems[:3])
            more = f'(还有 {len(problems) - 3} 处)' if len(problems) > 3 else ''
            tip = '　可以点「自动修正」' if fixable else ''
            self.problems.setText(f'{head}{more}{tip}')
            self.problems.setStyleSheet(f'color: {FAIL_RED};')

        self.deploy_button.setEnabled(
            not any(not p.fixable for p in problems) and bool(rows))

    # -- weights ----------------------------------------------------------

    def _find_weights(self) -> None:
        found = assets.locate()
        self._weights_dir = found.directory
        if found.ready:
            self.weights_label.setText(f'✓ 识别模型已就绪:{found.directory}')
            self.weights_label.setStyleSheet(f'color: {OK_GREEN};')
            self.download_button.setEnabled(False)
        else:
            self.weights_label.setText(
                '识别模型还没下载。机器人靠它认出人;缺了会看不清。'
                '请在能上网时点右边下载一次,以后就不用了。')
            self.weights_label.setStyleSheet(f'color: {FAIL_RED};')
            self.download_button.setEnabled(True)

    def _download(self) -> None:
        self.download_button.setEnabled(False)
        worker = DownloadWorker()
        def downloading(name: str, percent: int) -> None:
            self.progress.setFormat(f'下载 {name} {percent}%')
            self.progress.setValue(percent)

        worker.progress.connect(downloading)

        def finished(directory: str, error: str) -> None:
            if error:
                self._say(f'下载失败:{error}')
                QMessageBox.warning(self, '下载失败', error)
                self.download_button.setEnabled(True)
            else:
                self._say(f'识别模型已下载到 {directory}')
            self._find_weights()
            self.progress.setValue(0)
            self.progress.setFormat('尚未开始')

        worker.done.connect(finished)
        self._start(worker)

    # -- robot ------------------------------------------------------------

    def _detect(self) -> None:
        self.detect_button.setEnabled(False)
        self.identity.setText('正在连接…')
        self.identity.setStyleSheet(f'color: {WAIT_GREY};')
        worker = DetectWorker(DEFAULT_HOST, DEFAULT_USER, DEFAULT_PASSWORD)

        def finished(identity, error: str) -> None:
            self.detect_button.setEnabled(True)
            if identity is None:
                self.identity.setText(error)
                self.identity.setStyleSheet(f'color: {FAIL_RED};')
                self._say(f'检测失败:{error}')
            else:
                self.identity.setText(f'✓ {identity}')
                self.identity.setStyleSheet(f'color: {OK_GREEN};')
                self._say(f'已连接:{identity}')

        worker.done.connect(finished)
        self._start(worker)

    # -- deploying --------------------------------------------------------

    def _deploy(self) -> None:
        if not self._weights_dir:
            QMessageBox.warning(self, '还不能部署',
                                '请先下载识别模型,机器人需要它来认出人。')
            return

        self.deploy_button.setEnabled(False)
        self.result.setText('')
        self.progress.setValue(0)
        self._steps_done = 0
        self._steps_total = 8 if self.autostart.isChecked() else 5

        robot = Robot(DEFAULT_HOST, DEFAULT_USER, DEFAULT_PASSWORD)
        plan = Plan(package_dir=str(PACKAGE_ROOT),
                    phrases=P.apply_fixes(self._rows()),
                    weights_dir=self._weights_dir,
                    venue=self.venue.text().strip(),
                    install_service=self.autostart.isChecked(),
                    start_after=True)

        worker = DeployWorker(robot, plan)
        worker.event.connect(self._on_event)
        worker.done.connect(self._on_done)
        self._say('—— 开始部署 ——')
        self._start(worker)

    def _on_event(self, event: Event) -> None:
        if event.status is Status.RUNNING:
            self.progress.setFormat(f'{event.step}…')
            self._say(f'· {event.step}')
        elif event.status is Status.OK:
            self._steps_done += 1
            self.progress.setValue(
                int(self._steps_done * 100 / max(self._steps_total, 1)))
            self._say(f'  ✓ {event.message}' if event.message else '  ✓')
        elif event.status is Status.INFO:
            self._say(f'    {event.message}')
        else:
            self._say(f'  ✗ {event.message}')

    def _on_done(self, outcome) -> None:
        self.deploy_button.setEnabled(True)
        if outcome.ok:
            self.progress.setValue(100)
            self.progress.setFormat('完成')
            self.result.setText(
                '✓ 部署完成,机器人已经开始工作。\n'
                f'机器人:{outcome.identity}\n'
                f'实际运行:{outcome.startup_line}')
            self.result.setStyleSheet(f'color: {OK_GREEN};')
            self._say('—— 完成 ——')
        else:
            self.progress.setFormat('未完成')
            self.result.setText(
                '✗ 没有部署成功。下面「过程记录」的最后一行说明了原因。')
            self.result.setStyleSheet(f'color: {FAIL_RED};')
            self._say('—— 未完成 ——')

    # -- plumbing ---------------------------------------------------------

    def _say(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())

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
    app = QApplication(sys.argv)
    app.setApplicationName('X2 迎宾部署')
    window = Deployer()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
