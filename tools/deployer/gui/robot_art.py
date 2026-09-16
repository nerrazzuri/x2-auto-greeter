"""The photograph of the X2, and a panel that keeps it in proportion.

The left column holds everything that is a fixed size -- a status line, a
button, a menu -- so it ran out of content halfway down and the window read as
unfinished next to the greeting table. The robot fills the rest of it. That is
its whole job: it is the only element here allowed to take whatever height is
going, so the two columns end level however the window is sized.

The picture is a real photograph rather than the drawing that was here before,
and it is cut out to a transparent ground by `cutout.py` so that it sits on
whatever colour the customer's theme gives the panel. It is scaled on the way
in, never upscaled past its own resolution, and drawn standing on the bottom
edge -- the robot should look like it is on the floor, not floating in the
middle of a gap.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

FILE = 'x2.png'


def path() -> Path:
    """Where the picture is, frozen or from source.

    Frozen, build.py puts it under art/ in the bundle -- not next to the
    payload meant for the robot, which is uploaded wholesale.
    """
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        return Path(bundle) / 'art' / FILE
    return Path(__file__).resolve().parent / FILE


def photograph() -> QPixmap:
    """The robot, or a null pixmap if the file did not come along.

    A missing picture must not be fatal: it is decoration, and the deployment
    works perfectly well without it.
    """
    return QPixmap(str(path()))


class Portrait(QWidget):
    """The robot, as tall as the space allows, standing on the bottom edge."""

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._source = photograph()
        self._scaled = QPixmap()
        self._wanted = QSize()
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def has_picture(self) -> bool:
        return not self._source.isNull()

    def sizeHint(self) -> QSize:
        return QSize(240, 300)

    def minimumSizeHint(self) -> QSize:
        # Nothing: a short window must be free to squeeze this to nothing
        # rather than push the panels above it off the bottom.
        return QSize(0, 0)

    def paintEvent(self, _event) -> None:
        if self._source.isNull():
            return
        area = self.rect()
        if area.width() < 24 or area.height() < 24:
            return

        # Scaled in device pixels and told its own ratio, so the picture is
        # sharp on a high-density screen instead of a blown-up 1x copy.
        ratio = self.devicePixelRatioF()
        wanted = QSize(int(area.width() * ratio), int(area.height() * ratio))
        # Compared against what was asked for, not against what came back:
        # scaled() clamps to the aspect ratio, so the returned size almost
        # never equals the request and every paint would rescale.
        if self._wanted != wanted:
            self._scaled = self._source.scaled(
                wanted, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._scaled.setDevicePixelRatio(ratio)
            self._wanted = wanted

        width = self._scaled.width() / ratio
        height = self._scaled.height() / ratio
        painter = QPainter(self)
        painter.drawPixmap(int(area.x() + (area.width() - width) / 2),
                           int(area.y() + area.height() - height),
                           self._scaled)
