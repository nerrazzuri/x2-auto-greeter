"""A small drawing of the robot, painted rather than shipped.

An image file would have to survive PyInstaller, be found again at runtime
from inside the bundle, and come in two versions for two themes. This is forty
lines of QPainter that scale to any size, take their colour from the palette
in force, and cannot go missing.

It is an X2 in the pose the product is about: standing still, one arm up,
greeting somebody. Geometric rather than cute -- this sits above a panel about
network addresses, and a cartoon would be at odds with everything around it.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap


def greeter(width: int = 132, height: int = 120,
            ink: str = '#0f6d78', accent: str = '#2c6a45') -> QPixmap:
    """The robot, waving, on a transparent ground."""
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # One unit = one hundredth of the drawing, so every measurement below
    # reads as a proportion and the whole thing scales with the widget.
    ux, uy = width / 100.0, height / 100.0
    body = QColor(ink)
    line = QPen(body, max(1.6, 2.0 * ux), Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    painter.setPen(line)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Head, with the visor the X2 actually has rather than a face.
    head = QRectF(32 * ux, 14 * uy, 36 * ux, 26 * uy)
    painter.drawRoundedRect(head, 10 * ux, 10 * ux)
    visor = QColor(body)
    visor.setAlpha(45)
    painter.setBrush(visor)
    painter.drawRoundedRect(QRectF(37 * ux, 21 * uy, 26 * ux, 11 * uy),
                            5 * ux, 5 * ux)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Two eyes, because a visor alone reads as a letterbox.
    painter.setBrush(body)
    for dx in (44, 56):
        painter.drawEllipse(QPointF(dx * ux, 26.5 * uy), 2.2 * ux, 2.2 * ux)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    painter.drawLine(QPointF(50 * ux, 14 * uy), QPointF(50 * ux, 8 * uy))
    painter.setBrush(body)
    painter.drawEllipse(QPointF(50 * ux, 6.5 * uy), 2.4 * ux, 2.4 * ux)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    painter.drawLine(QPointF(50 * ux, 40 * uy), QPointF(50 * ux, 45 * uy))

    # Torso.
    painter.drawRoundedRect(QRectF(34 * ux, 45 * uy, 32 * ux, 30 * uy),
                            7 * ux, 7 * ux)

    # The raised arm is the whole point of the picture.
    painter.drawLine(QPointF(34 * ux, 52 * uy), QPointF(22 * ux, 60 * uy))
    painter.drawLine(QPointF(22 * ux, 60 * uy), QPointF(19 * ux, 72 * uy))
    painter.drawLine(QPointF(66 * ux, 52 * uy), QPointF(78 * ux, 44 * uy))
    painter.drawLine(QPointF(78 * ux, 44 * uy), QPointF(82 * ux, 31 * uy))

    hand = QColor(accent)
    painter.setBrush(hand)
    painter.setPen(QPen(hand, max(1.4, 1.6 * ux)))
    painter.drawEllipse(QPointF(83 * ux, 28 * uy), 3.6 * ux, 3.6 * ux)
    painter.setPen(line)
    painter.setBrush(body)
    painter.drawEllipse(QPointF(18.5 * ux, 74 * uy), 3.0 * ux, 3.0 * ux)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Legs, planted: this robot never walks under the greeter's control, and
    # the drawing should not suggest otherwise.
    painter.drawLine(QPointF(43 * ux, 75 * uy), QPointF(42 * ux, 90 * uy))
    painter.drawLine(QPointF(57 * ux, 75 * uy), QPointF(58 * ux, 90 * uy))
    painter.drawLine(QPointF(37 * ux, 91 * uy), QPointF(47 * ux, 91 * uy))
    painter.drawLine(QPointF(53 * ux, 91 * uy), QPointF(63 * ux, 91 * uy))

    painter.end()
    return pixmap
