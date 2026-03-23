"""
PyQt5 transparent overlay — draws the 360° radar.

Features:
  - Frameless, transparent, always-on-top, click-through window
  - Circular radar with self-car in centre
  - Other cars rendered as rounded rectangles with colour coding
  - Dynamic opacity (方案 C): dim when alone, bright when cars nearby
  - Draggable via Alt+click (to reposition on screen)
"""

from __future__ import annotations

import math
import sys
from typing import List

from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient,
    QFont, QPainterPath,
)
from PyQt5.QtWidgets import QWidget

from .config import RadarConfig
from .radar_calc import RadarBlip


def _qcolor(rgba_tuple) -> QColor:
    return QColor(*rgba_tuple)


class RadarWidget(QWidget):
    """The actual radar drawing surface."""

    def __init__(self, cfg: RadarConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.blips: List[RadarBlip] = []
        self.current_opacity: float = cfg.inactive_opacity
        self._target_opacity: float = cfg.inactive_opacity
        self.recording_progress: float = -1.0   # -1 = not recording
        self.spline_ready: bool = False
        self.connected: bool = False
        self.track_name: str = ''

        self.setFixedSize(cfg.radar_size, cfg.radar_size)

    # ------------------------------------------------------------------
    #  Public
    # ------------------------------------------------------------------

    def set_blips(self, blips: List[RadarBlip]):
        self.blips = blips
        has_nearby = any(b.distance < self.cfg.nearby_distance for b in blips)
        self._target_opacity = (
            self.cfg.active_opacity if has_nearby
            else self.cfg.inactive_opacity
        )
        self.update()

    def animate_opacity(self):
        diff = self._target_opacity - self.current_opacity
        if abs(diff) > 0.005:
            self.current_opacity += diff * self.cfg.fade_speed
        else:
            self.current_opacity = self._target_opacity

    # ------------------------------------------------------------------
    #  Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        self.animate_opacity()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setOpacity(self.current_opacity)

        size = self.cfg.radar_size
        cx, cy = size / 2, size / 2
        radius = size / 2 - 6

        self._draw_background(p, cx, cy, radius)
        self._draw_rings(p, cx, cy, radius)
        self._draw_cars(p, cx, cy, radius)
        self._draw_self_car(p, cx, cy)
        self._draw_status(p, cx, cy, radius)

        p.end()

    def _draw_background(self, p: QPainter, cx, cy, radius):
        grad = QRadialGradient(cx, cy, radius)
        bg = _qcolor(self.cfg.bg_colour)
        grad.setColorAt(0, QColor(bg.red(), bg.green(), bg.blue(), bg.alpha()))
        grad.setColorAt(1, QColor(bg.red(), bg.green(), bg.blue(), int(bg.alpha() * 0.4)))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

    def _draw_rings(self, p: QPainter, cx, cy, radius):
        ring_col = _qcolor(self.cfg.ring_colour)
        pen = QPen(ring_col, 1.0, Qt.DotLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        # concentric rings at 25%, 50%, 75%, 100% range
        for frac in (0.25, 0.5, 0.75, 1.0):
            r = radius * frac
            p.drawEllipse(QPointF(cx, cy), r, r)

        # crosshair lines
        pen.setStyle(Qt.DotLine)
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))

    def _draw_self_car(self, p: QPainter, cx, cy):
        cfg = self.cfg
        scale = (self.cfg.radar_size / 2 - 6) / cfg.range_metres
        vs = cfg.car_visual_scale
        w = cfg.self_car_width * scale * vs
        h = cfg.self_car_length * scale * vs
        col = _qcolor(cfg.self_colour)

        # car body
        p.setPen(QPen(col, 1.5))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 60))
        rect = QRectF(cx - w / 2, cy - h / 2, w, h)
        p.drawRoundedRect(rect, 2, 2)

        # direction indicator (small triangle pointing up)
        tri_size = max(w * 0.4, 3)
        path = QPainterPath()
        path.moveTo(cx, cy - h / 2 - 2)
        path.lineTo(cx - tri_size / 2, cy - h / 2 + tri_size - 2)
        path.lineTo(cx + tri_size / 2, cy - h / 2 + tri_size - 2)
        path.closeSubpath()
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawPath(path)

    def _draw_cars(self, p: QPainter, cx, cy, radius):
        cfg = self.cfg
        scale = radius / cfg.range_metres

        for blip in self.blips:
            px = cx + blip.rx * scale
            py = cy - blip.ry * scale

            dx, dy = px - cx, py - cy
            dist_px = math.sqrt(dx * dx + dy * dy)
            if dist_px > radius - 4:
                s = (radius - 4) / max(dist_px, 1)
                px = cx + dx * s
                py = cy + dy * s

            if blip.is_lapped:
                col = _qcolor(cfg.car_colour_lapped)
            elif blip.distance < cfg.danger_distance:
                col = _qcolor(cfg.car_colour_near)
            else:
                t = min(blip.distance / cfg.range_metres, 1.0)
                near = _qcolor(cfg.car_colour_near)
                far = _qcolor(cfg.car_colour_far)
                col = QColor(
                    int(near.red() + (far.red() - near.red()) * t),
                    int(near.green() + (far.green() - near.green()) * t),
                    int(near.blue() + (far.blue() - near.blue()) * t),
                    int(near.alpha() + (far.alpha() - near.alpha()) * t),
                )

            vs = cfg.car_visual_scale
            w = cfg.other_car_width * scale * vs
            h = cfg.other_car_length * scale * vs
            w = max(w, 8)
            h = max(h, 14)

            p.setPen(QPen(col, 1.2))
            fill = QColor(col.red(), col.green(), col.blue(), int(col.alpha() * 0.5))
            p.setBrush(fill)
            rect = QRectF(px - w / 2, py - h / 2, w, h)
            p.drawRoundedRect(rect, 2, 2)

    def _draw_status(self, p: QPainter, _cx, cy, radius):
        """Draw recording progress or connection status."""
        font = QFont('Arial', 9)
        p.setFont(font)

        if not self.connected:
            p.setPen(QColor(200, 200, 200, 180))
            p.drawText(
                QRectF(0, cy + radius * 0.55, self.cfg.radar_size, 20),
                Qt.AlignCenter, 'Waiting for iRacing...'
            )
        elif self.recording_progress >= 0 and not self.spline_ready:
            pct = int(self.recording_progress * 100)
            p.setPen(QColor(255, 200, 0, 220))
            p.drawText(
                QRectF(0, cy + radius * 0.55, self.cfg.radar_size, 20),
                Qt.AlignCenter, f'Recording track: {pct}%'
            )
        elif self.track_name:
            p.setPen(QColor(160, 160, 160, 100))
            p.drawText(
                QRectF(0, cy + radius * 0.70, self.cfg.radar_size, 16),
                Qt.AlignCenter, self.track_name
            )


# ======================================================================
#  Overlay window
# ======================================================================

class OverlayWindow(QWidget):
    """
    Frameless, transparent, always-on-top overlay.
    Hosts the RadarWidget and handles drag to reposition.
    """

    def __init__(self, cfg: RadarConfig):
        super().__init__()
        self.cfg = cfg
        self._drag_pos = None

        # window flags
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # hides from taskbar
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # on Windows: make click-through unless Alt is held
        if sys.platform == 'win32':
            try:
                import ctypes
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_TRANSPARENT = 0x00000020
                WS_EX_LAYERED = 0x00080000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE,
                    style | WS_EX_TRANSPARENT | WS_EX_LAYERED
                )
            except Exception:
                pass

        self.setFixedSize(cfg.radar_size, cfg.radar_size)

        # resolve position from preset using screen geometry
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.size()
            cfg.compute_position(geo.width(), geo.height())
        else:
            cfg.compute_position(1920, 1080)
        self.move(cfg.window_x, cfg.window_y)

        self.radar = RadarWidget(cfg, self)
        self.radar.move(0, 0)

    # ------------------------------------------------------------------
    #  Drag to reposition (Alt + left click)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None:
            self._drag_pos = None
            self.cfg.position_preset = 'custom'
            self.cfg.window_x = self.x()
            self.cfg.window_y = self.y()
            self.cfg.save()
            event.accept()
