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
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.gui import QgsElevationProfileCanvas, QgsPlotCanvasItem

from .layer_setup import MANHOLE_DEFAULT_PX_WIDTH, _resolve_manhole_anchors

# Pixel height of the dashed "no level data" shaft. Both manhole levels are
# unknown, so the true height can't be derived — this is a fixed visual stand-in
# anchored at the adjacent reach invert, not a fabricated elevation.
NO_LEVEL_SHAFT_PX_HEIGHT = 60.0


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

    def dashes(self):
        return self._dashes

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

    def _drawCoverLine(self, painter, cover_pt, half_width, cover_pen):
        painter.setPen(cover_pen)
        painter.drawLine(
            QPointF(cover_pt.x() - half_width - 3, cover_pt.y()),
            QPointF(cover_pt.x() + half_width + 3, cover_pt.y()),
        )

    def paint(self, painter, option=None, widget=None):
        if painter is None or not painter.isActive() or not self._dashes:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot_area = self._canvas.plotArea() if hasattr(self._canvas, "plotArea") else None

        shaft_color = getattr(self._canvas, "_manhole_shaft_color", QColor("#6E4C1E"))
        cover_color = getattr(self._canvas, "_manhole_cover_color", QColor("#2C3E50"))
        default_px_width = getattr(self._canvas, "_manhole_default_px_width", MANHOLE_DEFAULT_PX_WIDTH)

        shaft_pen = QPen(shaft_color, 1.5)
        shaft_pen.setStyle(Qt.PenStyle.SolidLine)
        shaft_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        cover_pen = QPen(cover_color, 2.5)
        cover_pen.setStyle(Qt.PenStyle.SolidLine)
        cover_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        any_x = False
        any_question = False

        for dash in self._dashes:
            distance = dash.get("distance")
            cover_level = dash.get("cover_level")
            bottom_level = dash.get("bottom_level")
            shaft_width_px = dash.get("width", default_px_width)
            cover_missing = dash.get("cover_level_missing", False)
            bottom_missing = dash.get("bottom_level_missing", False)

            if distance is None:
                continue

            # No cover and no bottom level: nothing to position the shaft with.
            # Anchor it to the adjacent reach invert and draw a red dashed shaft
            # topped with "?" — never fabricate a level (anchor_level is None
            # when no reach is near, in which case we skip rather than guess).
            if cover_missing and bottom_missing:
                if self._drawNoLevelShaft(
                    painter, distance, dash.get("anchor_level"), shaft_width_px, plot_area
                ):
                    any_question = True
                continue

            if cover_level is None and bottom_level is None:
                continue

            anchor_cover, anchor_bottom = _resolve_manhole_anchors(cover_level, bottom_level)

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

                self._drawCoverLine(painter, cover_pt, half_width, cover_pen)
            elif bottom_missing:
                self._drawCoverLine(painter, cover_pt, half_width, cover_pen)
                x_center = QPointF(cover_pt.x(), cover_pt.y() + 18.0)
                self._drawMissingDataX(painter, x_center)
                self._drawMissingLabel(painter, x_center, "no bottom level")
                any_x = True
            else:
                painter.setPen(shaft_pen)
                painter.drawLine(
                    QPointF(bottom_pt.x() - half_width, bottom_pt.y()),
                    QPointF(bottom_pt.x() + half_width, bottom_pt.y()),
                )
                x_center = QPointF(bottom_pt.x(), bottom_pt.y() - 18.0)
                self._drawMissingDataX(painter, x_center)
                self._drawMissingLabel(painter, x_center, "no cover level")
                any_x = True

        if any_x or any_question:
            self._drawMissingLegend(painter, plot_area, any_x, any_question)

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

    def _missingDataFont(self):
        font = QFont(self._canvas.font())
        font.setPointSize(8)
        return font

    def _drawMissingLabel(self, painter, x_center, text):
        """Small red caption placed to the right of a missing-data marker."""
        painter.setFont(self._missingDataFont())
        painter.setPen(QPen(QColor("#FF0000")))
        painter.drawText(QPointF(x_center.x() + 12.0, x_center.y() + 4.0), text)

    def _drawUnknownMark(self, painter, center):
        """Bold red '?' marking a manhole whose cover and bottom are both unknown."""
        font = QFont(self._canvas.font())
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#FF0000")))
        painter.drawText(
            QRectF(center.x() - 8.0, center.y() - 10.0, 16.0, 20.0),
            Qt.AlignmentFlag.AlignCenter,
            "?",
        )

    def _drawNoLevelShaft(self, painter, distance, anchor_level, shaft_width_px, plot_area):
        """
        Red dashed shaft + '?' for a manhole with no cover and no bottom level.

        Anchored at the adjacent reach invert (``anchor_level``); drawn a fixed
        pixel height upward because the real shaft height is unknown. Returns
        True when something was drawn, False when there is nothing to anchor to.
        """
        if anchor_level is None:
            return False
        bottom_pt = self._plotPointToCanvasPoint(distance, anchor_level)
        if bottom_pt is None:
            return False
        if plot_area is not None and not plot_area.contains(bottom_pt):
            return False

        half_width = shaft_width_px / 2.0
        top_y = bottom_pt.y() - NO_LEVEL_SHAFT_PX_HEIGHT
        left = bottom_pt.x() - half_width
        right = bottom_pt.x() + half_width

        dashed_pen = QPen(QColor("#FF0000"), 1.5)
        dashed_pen.setStyle(Qt.PenStyle.DashLine)
        dashed_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(dashed_pen)
        painter.drawLine(QPointF(left, bottom_pt.y()), QPointF(left, top_y))
        painter.drawLine(QPointF(right, bottom_pt.y()), QPointF(right, top_y))
        painter.drawLine(QPointF(left, top_y), QPointF(right, top_y))
        painter.drawLine(QPointF(left, bottom_pt.y()), QPointF(right, bottom_pt.y()))

        q_center = QPointF(bottom_pt.x(), top_y - 9.0)
        self._drawUnknownMark(painter, q_center)
        self._drawMissingLabel(painter, q_center, "no level data")
        return True

    def _drawMissingLegend(self, painter, plot_area, any_x, any_question):
        """Top-left legend for whichever missing-data markers are present."""
        if plot_area is not None and not plot_area.isEmpty():
            x0 = plot_area.left() + 10.0
            y0 = plot_area.top() + 14.0
        else:
            top_left = self._rect.topLeft()
            x0 = top_left.x() + 12.0
            y0 = top_left.y() + 16.0

        painter.setFont(self._missingDataFont())
        line_y = y0
        if any_x:
            self._drawMissingDataX(painter, QPointF(x0 + 6.0, line_y), x_size=5.0)
            painter.setPen(QPen(QColor("#FF0000")))
            painter.drawText(QPointF(x0 + 18.0, line_y + 4.0), "= missing level")
            line_y += 16.0
        if any_question:
            painter.setPen(QPen(QColor("#FF0000")))
            painter.drawText(QPointF(x0 + 1.0, line_y + 5.0), "?")
            painter.drawText(QPointF(x0 + 18.0, line_y + 4.0), "= no level data")


class TwwElevationProfileCanvas(QgsElevationProfileCanvas):
    """
    Custom elevation profile canvas to ensure mouse move events reach hover logic.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_callback = None
        self._leave_callback = None
        self._manhole_shaft_color = QColor("#6E4C1E")  # Brown color for manhole shaft walls
        self._manhole_cover_color = QColor("#2C3E50")  # Dark gray for manhole cover
        self._manhole_default_px_width = MANHOLE_DEFAULT_PX_WIDTH
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

    def setHoverHandlers(self, move_handler, leave_handler):
        """Wire hover/leave handlers after ProfileHoverManager is created."""
        self._hover_callback = move_handler
        self._leave_callback = leave_handler

    def manholeDefaultPxWidth(self):
        return self._manhole_default_px_width

    def getManholeDashes(self):
        if self._manhole_item is None:
            return []
        return self._manhole_item.dashes()

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
        if self._manhole_item is not None:
            self._manhole_item.setDashes(dashes or [])
