# -----------------------------------------------------------
#
# Elevation Profile Canvas
# Copyright (C) 2026  TEKSI Contributors and Peter Zhao
# -----------------------------------------------------------
#
# licensed under the terms of GNU GPL 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# ---------------------------------------------------------------------

from qgis.core import QgsProfilePoint
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.gui import QgsElevationProfileCanvas, QgsPlotCanvasItem


class ManholeDashPlotItem(QgsPlotCanvasItem):
    """
    Overlay item for manhole shaft walls and cover lines on the profile canvas.

    QGIS 4 no longer invokes canvas drawForeground() overrides from Python;
    QgsPlotCanvasItem is the supported overlay mechanism (same as crosshairs).
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._dashes = []
        self._rect = QRectF()
        self.setZValue(90)
        self.updateRect()

    def updateRect(self):
        self._rect = QRectF(self._canvas.rect())
        self.prepareGeometryChange()
        self.setPos(self._rect.topLeft())
        self.update()

    def boundingRect(self):
        return self._rect

    def setDashes(self, dashes):
        self._dashes = dashes or []
        self.update()

    def _plotPointToCanvasPoint(self, distance, elevation):
        if not hasattr(self._canvas, "plotPointToCanvasPoint"):
            return None
        profile_point = QgsProfilePoint(float(distance), float(elevation))
        try:
            canvas_point = self._canvas.plotPointToCanvasPoint(profile_point)
        except (TypeError, ValueError):
            return None
        if canvas_point is None:
            return None
        if hasattr(canvas_point, "isEmpty") and canvas_point.isEmpty():
            return None
        return QPointF(canvas_point.x(), canvas_point.y())

    def paint(self, painter, option=None, widget=None):
        if painter is None or not painter.isActive() or not self._dashes:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot_area = self._canvas.plotArea() if hasattr(self._canvas, "plotArea") else None

        shaft_color = getattr(self._canvas, "_manhole_shaft_color", QColor("#6E4C1E"))
        cover_color = getattr(self._canvas, "_manhole_cover_color", QColor("#2C3E50"))
        default_px_width = getattr(self._canvas, "_manhole_default_px_width", 10)

        shaft_pen = QPen(shaft_color, 1.5)
        shaft_pen.setStyle(Qt.PenStyle.SolidLine)
        shaft_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        cover_pen = QPen(cover_color, 2.5)
        cover_pen.setStyle(Qt.PenStyle.SolidLine)
        cover_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        for dash in self._dashes:
            distance = dash.get("distance")
            cover_level = dash.get("cover_level")
            bottom_level = dash.get("bottom_level")
            shaft_width_px = dash.get("width", default_px_width)
            cover_missing = dash.get("cover_level_missing", False) or cover_level is None
            bottom_missing = dash.get("bottom_level_missing", False) or bottom_level is None

            if distance is None or (cover_level is None and bottom_level is None):
                continue

            anchor_cover = cover_level if cover_level is not None else bottom_level
            anchor_bottom = bottom_level if bottom_level is not None else cover_level

            cover_pt = self._plotPointToCanvasPoint(distance, anchor_cover)
            bottom_pt = self._plotPointToCanvasPoint(distance, anchor_bottom)
            if cover_pt is None or bottom_pt is None:
                continue

            if plot_area is not None:
                if not plot_area.contains(cover_pt) and not plot_area.contains(bottom_pt):
                    continue

            half_width = shaft_width_px / 2.0

            if not cover_missing and not bottom_missing:
                left_top = QPointF(cover_pt.x() - half_width, cover_pt.y())
                left_bottom = QPointF(bottom_pt.x() - half_width, bottom_pt.y())
                right_top = QPointF(cover_pt.x() + half_width, cover_pt.y())
                right_bottom = QPointF(bottom_pt.x() + half_width, bottom_pt.y())

                painter.setPen(shaft_pen)
                painter.drawLine(left_top, left_bottom)
                painter.drawLine(right_top, right_bottom)
                painter.drawLine(left_bottom, right_bottom)

                painter.setPen(cover_pen)
                painter.drawLine(
                    QPointF(cover_pt.x() - half_width - 3, cover_pt.y()),
                    QPointF(cover_pt.x() + half_width + 3, cover_pt.y()),
                )
            elif bottom_missing:
                painter.setPen(cover_pen)
                painter.drawLine(
                    QPointF(cover_pt.x() - half_width - 3, cover_pt.y()),
                    QPointF(cover_pt.x() + half_width + 3, cover_pt.y()),
                )
                self._drawMissingDataX(
                    painter, QPointF(cover_pt.x(), cover_pt.y() + 18.0)
                )
            else:
                painter.setPen(shaft_pen)
                painter.drawLine(
                    QPointF(bottom_pt.x() - half_width, bottom_pt.y()),
                    QPointF(bottom_pt.x() + half_width, bottom_pt.y()),
                )
                self._drawMissingDataX(
                    painter, QPointF(bottom_pt.x(), bottom_pt.y() - 18.0)
                )

    def _drawMissingDataX(self, painter, center, x_size=8.0):
        x_pen = QPen(QColor("#FF0000"), 2.5)
        x_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(x_pen)
        painter.drawLine(
            QPointF(center.x() - x_size, center.y() - x_size),
            QPointF(center.x() + x_size, center.y() + x_size),
        )
        painter.drawLine(
            QPointF(center.x() + x_size, center.y() - x_size),
            QPointF(center.x() - x_size, center.y() + x_size),
        )


class TwwElevationProfileCanvas(QgsElevationProfileCanvas):
    """
    Custom elevation profile canvas to ensure mouse move events reach hover logic.
    """

    def __init__(self, parent=None, hover_callback=None, leave_callback=None):
        super().__init__(parent)
        self._hover_callback = hover_callback
        self._leave_callback = leave_callback
        self._manhole_dashes = []
        self._manhole_shaft_color = QColor("#6E4C1E")  # Brown color for manhole shaft walls
        self._manhole_cover_color = QColor("#2C3E50")  # Dark gray for manhole cover
        self._manhole_default_px_width = 10  # Default pixel width for manhole shaft
        self.setMouseTracking(True)
        if hasattr(self, "viewport"):
            try:
                self.viewport().setMouseTracking(True)
            except Exception:
                pass

        # QGIS >= 3.44: disable subsections indicator (vertical lines at curve vertices).
        if hasattr(self, "setSubsectionsSymbol"):
            try:
                self.setSubsectionsSymbol(None)
            except Exception:
                pass

        self._manhole_item = ManholeDashPlotItem(self)
        if hasattr(self, "plotAreaChanged"):
            self.plotAreaChanged.connect(self._onPlotAreaChanged)

    def _onPlotAreaChanged(self):
        if self._manhole_item is not None:
            self._manhole_item.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._manhole_item is not None:
            self._manhole_item.updateRect()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._hover_callback:
            self._hover_callback(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._leave_callback:
            self._leave_callback(event)

    def setManholeDashes(self, dashes):
        self._manhole_dashes = dashes or []
        if self._manhole_item is not None:
            self._manhole_item.setDashes(self._manhole_dashes)
