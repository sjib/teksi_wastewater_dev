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

from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPen
from qgis.gui import QgsElevationProfileCanvas


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
        self._manhole_default_width = 1000  # Default manhole diameter in mm (1m)
        self._manhole_default_px_width = 10  # Default pixel width for manhole shaft
        self.setMouseTracking(True)
        if hasattr(self, "viewport"):
            try:
                self.viewport().setMouseTracking(True)
            except Exception:
                pass

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
        self.update()

    def _plotPointToCanvasPoint(self, plot_point):
        if plot_point is None or not hasattr(self, "plotPointToCanvasPoint"):
            return None
        try:
            return self.plotPointToCanvasPoint(plot_point)
        except TypeError:
            try:
                from qgis.core import QgsProfilePoint

                if isinstance(plot_point, QPointF):
                    profile_point = QgsProfilePoint(float(plot_point.x()), float(plot_point.y()))
                else:
                    profile_point = QgsProfilePoint(float(plot_point[0]), float(plot_point[1]))
                return self.plotPointToCanvasPoint(profile_point)
            except Exception:
                return None

    def drawForeground(self, painter, rect):
        """
        Override drawForeground to draw custom manhole dashes.
        Reach lines are now rendered via QGIS native API using temp layer with Z values.
        """
        super().drawForeground(painter, rect)

        # Safety checks
        if not hasattr(self, "plotPointToCanvasPoint"):
            return
        if not self._manhole_dashes:
            return
        if painter is None or not painter.isActive():
            return

        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            plot_area = self.plotArea() if hasattr(self, "plotArea") else None

            # Draw manhole shafts (double-line rectangle + cover line)
            for dash in self._manhole_dashes:
                distance = dash.get("distance")
                cover_level = dash.get("cover_level")
                bottom_level = dash.get("bottom_level")
                shaft_width_px = dash.get("width", self._manhole_default_px_width)

                if distance is None or cover_level is None or bottom_level is None:
                    continue

                # Convert plot coordinates to canvas coordinates
                cover_canvas = self._plotPointToCanvasPoint(
                    QPointF(float(distance), float(cover_level))
                )
                bottom_canvas = self._plotPointToCanvasPoint(
                    QPointF(float(distance), float(bottom_level))
                )
                if cover_canvas is None or bottom_canvas is None:
                    continue

                cover_pt = QPointF(cover_canvas.x(), cover_canvas.y())
                bottom_pt = QPointF(bottom_canvas.x(), bottom_canvas.y())

                if plot_area is not None:
                    if not plot_area.contains(cover_pt) and not plot_area.contains(bottom_pt):
                        continue

                half_width = shaft_width_px / 2.0

                # Draw left shaft wall (vertical line)
                left_top = QPointF(cover_pt.x() - half_width, cover_pt.y())
                left_bottom = QPointF(bottom_pt.x() - half_width, bottom_pt.y())

                # Draw right shaft wall (vertical line)
                right_top = QPointF(cover_pt.x() + half_width, cover_pt.y())
                right_bottom = QPointF(bottom_pt.x() + half_width, bottom_pt.y())

                # Shaft wall pen (brown, 1.5px)
                shaft_pen = QPen(self._manhole_shaft_color, 1.5)
                shaft_pen.setStyle(Qt.SolidLine)
                shaft_pen.setCapStyle(Qt.FlatCap)
                painter.setPen(shaft_pen)

                # Draw shaft walls
                painter.drawLine(left_top, left_bottom)
                painter.drawLine(right_top, right_bottom)

                # Draw bottom line (connect left and right at bottom)
                # Use red circle if bottom_level data is missing
                if dash.get("bottom_level_missing", False):
                    # Draw red circle at bottom center (same size as cover marker)
                    circle_radius = 10.0
                    painter.setPen(QPen(QColor("#FF0000"), 1.5))
                    painter.setBrush(QBrush(QColor("#FF0000")))
                    painter.drawEllipse(bottom_pt, circle_radius, circle_radius)
                    painter.setBrush(QBrush())  # Reset brush
                    painter.setPen(shaft_pen)  # Restore shaft pen
                else:
                    painter.drawLine(left_bottom, right_bottom)

                # Draw cover line (horizontal line at top, slightly wider)
                cover_pen = QPen(self._manhole_cover_color, 2.5)
                cover_pen.setStyle(Qt.SolidLine)
                cover_pen.setCapStyle(Qt.FlatCap)
                painter.setPen(cover_pen)

                cover_left = QPointF(cover_pt.x() - half_width - 3, cover_pt.y())
                cover_right = QPointF(cover_pt.x() + half_width + 3, cover_pt.y())
                painter.drawLine(cover_left, cover_right)
        except Exception:
            # Silently ignore drawing errors to prevent crashes
            pass
