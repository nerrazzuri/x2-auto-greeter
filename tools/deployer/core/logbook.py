"""A log file that survives the program dying.

The window has a progress panel, and it is enough right up until the moment it
is not: when the process aborts, everything in that panel goes with it. A
customer then has a window that vanished and nothing to send anybody. That is
the failure this module exists for, so it is built around the crash rather
than around the happy path.

Four things are captured, because a Qt program can die in four different
places and only one of them is an ordinary Python exception:

  * exceptions on the main thread          -- sys.excepthook
  * exceptions inside a worker thread      -- threading.excepthook
  * Qt's own complaints                    -- qInstallMessageHandler, which is
    where "QThread: Destroyed while thread is still running" appears, moments
    before the abort it causes
  * the abort itself                       -- faulthandler, which writes a C
    and Python traceback as the process is going down

The file is opened unbuffered and written line by line. A buffered log loses
the last few lines, which are the only ones that matter.
"""
from __future__ import annotations

import datetime
import faulthandler
import io
import platform
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional, TextIO

_handle: Optional[TextIO] = None
_path: Optional[Path] = None
_lock = threading.Lock()


def directory() -> Path:
    """Where logs are kept. Under the user's home: on Windows the program may
    well sit in Program Files, which is not writable."""
    return Path.home() / '.x2-deployer' / 'logs'


def path() -> Optional[Path]:
    return _path


def start(app_version: str = '') -> Path:
    """Open today's log and take over every way the program can die."""
    global _handle, _path

    if _handle is not None:
        return _path

    directory().mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    _path = directory() / f'deploy-{stamp}.log'
    # Line buffered: a crash must not take the last few lines with it, and
    # those are the only ones anybody will read.
    _handle = open(_path, 'w', encoding='utf-8', buffering=1)

    write('X2 deployer log')
    write(f'  version   {app_version or "(source)"}')
    write(f'  python    {sys.version.split()[0]}')
    write(f'  platform  {platform.platform()}')
    write(f'  frozen    {bool(getattr(sys, "frozen", False))}')
    write(f'  log       {_path}')
    write('-' * 60)

    # Writes a C-level and Python traceback as the process aborts -- the only
    # thing that produces anything at all from a segfault or a Qt abort.
    faulthandler.enable(file=_handle, all_threads=True)

    sys.excepthook = _on_main_thread_exception
    threading.excepthook = _on_worker_thread_exception
    _install_qt_handler()
    return _path


def write(line: str) -> None:
    """One line into the log. Never raises: logging must not be what fails."""
    if _handle is None:
        return
    try:
        with _lock:
            stamp = datetime.datetime.now().strftime('%H:%M:%S')
            _handle.write(f'{stamp}  {line}\n')
    except Exception:                                   # noqa: BLE001
        pass


def write_exception(where: str, exc: BaseException) -> None:
    write(f'!! {where}: {type(exc).__name__}: {exc}')
    buffer = io.StringIO()
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=buffer)
    for line in buffer.getvalue().rstrip().splitlines():
        write(f'   {line}')


def _on_main_thread_exception(kind, value, tb) -> None:
    write_exception('unhandled exception', value)
    sys.__excepthook__(kind, value, tb)


def _on_worker_thread_exception(args) -> None:
    """Without this a worker's exception is printed to a console the customer
    does not have, and the window simply stops making progress."""
    if args.exc_value is not None:
        write_exception(f'unhandled exception in {args.thread.name}',
                        args.exc_value)


def _install_qt_handler() -> None:
    """Qt's own warnings, which is where the thread and object-lifetime
    complaints appear -- usually a line or two before the abort they cause."""
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:                                 # pragma: no cover
        return

    labels = {
        QtMsgType.QtDebugMsg: 'Qt debug',
        QtMsgType.QtInfoMsg: 'Qt info',
        QtMsgType.QtWarningMsg: 'Qt WARNING',
        QtMsgType.QtCriticalMsg: 'Qt CRITICAL',
        QtMsgType.QtFatalMsg: 'Qt FATAL',
    }

    def handler(kind, _context, message):
        write(f'[{labels.get(kind, "Qt")}] {message}')

    qInstallMessageHandler(handler)


def recent(limit: int = 5):
    """The newest log files, for a window offering to show them."""
    if not directory().is_dir():
        return []
    return sorted(directory().glob('deploy-*.log'),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
