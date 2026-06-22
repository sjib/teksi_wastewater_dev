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

import math

from qgis.core import QgsProfilePoint
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from qgis.gui import QgsElevationProfileCanvas, QgsPlotCanvasItem

from .layer_setup import (
    MANHOLE_DEFAULT_PX_WIDTH,
    _resolve_manhole_anchors,
    manhole_cover_offset_px,
    manhole_shaft_px,
    manhole_sump_offset_px,
    reach_band_px,
)

# Pixel height of the dashed "no level data" shaft. Both manhole levels are
# unknown, so the true height can't be derived — this is a fixed visual stand-in
# anchored at the adjacent reach invert, sized like a typical exaggerated shaft.
NO_LEVEL_SHAFT_PX_HEIGHT = 30.0

# Extra hover slack above the cover cap. The cap sits at the very top of a small
# box and users aim at it or a little above, so the hit rect extends this far up.
COVER_HOVER_MARGIN_PX = 16.0


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
        self._bands = []
        # (dash, QRectF) in canvas pixels for the structures actually drawn this
        # frame — hover hit-tests against these, since the shafts are drawn in
        # exaggerated pixel space, not at their true (collapsed) elevations.
        self._hit_rects = []
        # (dash, QRectF) for just the cover cap band, so hovering the cover can
        # show a dedicated cover tooltip distinct from the whole-manhole one.
        self._cover_hit_rects = []
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
        # Drop stale hit rects immediately: when dashes become empty, paint()
        # returns early and would otherwise leave last frame's rects hoverable.
        self._hit_rects = []
        self._cover_hit_rects = []
        self.update()

    def dashes(self):
        return self._dashes

    def setBands(self, bands):
        self._bands = bands or []
        self.update()

    def bands(self):
        return self._bands

    def hitRects(self):
        """(dash, QRectF) pairs for structures drawn this frame, for hover."""
        return self._hit_rects

    def coverHitRects(self):
        """(dash, QRectF) pairs for cover caps drawn this frame, for hover."""
        return self._cover_hit_rects

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

    def _drawCoverCap(self, painter, cover_pt, half_width, cover_pen):
        """Cover drawn as a bold line with short side brackets (a '⊓' cap)."""
        painter.setPen(cover_pen)
        left = cover_pt.x() - half_width - 3
        right = cover_pt.x() + half_width + 3
        y = cover_pt.y()
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.drawLine(QPointF(left, y), QPointF(left, y + 5))
        painter.drawLine(QPointF(right, y), QPointF(right, y + 5))

    def paint(self, painter, option=None, widget=None):
        if painter is None or not painter.isActive() or (not self._dashes and not self._bands):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot_area = self._canvas.plotArea() if hasattr(self._canvas, "plotArea") else None

        # Pipe bands first, so manhole shafts/cover lines draw on top of them.
        self._drawReachBands(painter, plot_area)

        # Reset before the early return so an empty-dash frame can't leave stale
        # hover rects from the previous frame.
        self._hit_rects = []
        self._cover_hit_rects = []

        if not self._dashes:
            return

        manhole_color = getattr(self._canvas, "_manhole_shaft_color", QColor("#6E4C1E"))
        structure_color = getattr(self._canvas, "_structure_shaft_color", QColor("#0E6655"))
        cover_color = getattr(self._canvas, "_manhole_cover_color", QColor("#2C3E50"))
        chamber_color = getattr(self._canvas, "_manhole_chamber_color", QColor("#FFFFFF"))
        default_px_width = getattr(self._canvas, "_manhole_default_px_width", MANHOLE_DEFAULT_PX_WIDTH)

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

            # Non-manhole structures (special_structure, discharge_point, ...) are
            # drawn in a distinct colour so the type reads at a glance.
            is_manhole = str(dash.get("ws_type") or "").lower() == "manhole"
            shaft_color = manhole_color if is_manhole else structure_color
            shaft_pen = QPen(shaft_color, 1.5)
            shaft_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

            # No cover and no bottom level: nothing to position the shaft with.
            # Anchor it to the pipe invert and draw a red dashed shaft topped
            # with "?" — never fabricate a level (invert_level is None when no
            # reach is near, in which case we skip rather than guess).
            if cover_missing and bottom_missing:
                rect = self._drawNoLevelShaft(
                    painter, distance, dash.get("invert_level"), shaft_width_px, plot_area
                )
                if rect is not None:
                    self._hit_rects.append((dash, rect))
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
            invert_level = dash.get("invert_level")

            if not cover_missing and not bottom_missing:
                # Build the chamber around the pipe invert (rp level), the true
                # connection point of the band: cover sits an exaggerated offset
                # above it, the floor (wn_bottom_level / sump) an exaggerated
                # offset below it. All offsets are pixels because the real
                # metre gaps are sub-pixel at overview zoom; their ratio is kept.
                if invert_level is not None:
                    invert_pt = self._plotPointToCanvasPoint(distance, invert_level)
                    if invert_pt is None:
                        continue
                    anchor_x = invert_pt.x()
                    top_y = invert_pt.y() - manhole_cover_offset_px(cover_level - invert_level)
                    floor_y = invert_pt.y() + manhole_sump_offset_px(invert_level - bottom_level)
                else:
                    # No pipe invert nearby: fall back to a single exaggerated
                    # shaft anchored at the true bottom level.
                    anchor_x = bottom_pt.x()
                    floor_y = bottom_pt.y()
                    top_y = bottom_pt.y() - manhole_shaft_px(cover_level - bottom_level)

                # Keep the cover inside the plot: a top-of-range manhole's
                # exaggerated cover can land above the canvas, where it can't be
                # hovered. Clamp the top down and keep a minimum chamber height.
                if plot_area is not None and not plot_area.isEmpty():
                    min_top = plot_area.top() + 2.0
                    if top_y < min_top:
                        top_y = min_top
                        floor_y = max(floor_y, top_y + 8.0)

                left_x = anchor_x - half_width
                right_x = anchor_x + half_width

                # Opaque chamber fill drawn over the pipe band so the pipe visibly
                # terminates at the chamber wall (it connects, not crosses).
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(chamber_color))
                painter.drawRect(QRectF(left_x, top_y, right_x - left_x, floor_y - top_y))
                painter.setBrush(Qt.BrushStyle.NoBrush)

                painter.setPen(shaft_pen)
                painter.drawLine(QPointF(left_x, top_y), QPointF(left_x, floor_y))
                painter.drawLine(QPointF(right_x, top_y), QPointF(right_x, floor_y))
                painter.drawLine(QPointF(left_x, floor_y), QPointF(right_x, floor_y))

                self._drawCoverCap(painter, QPointF(anchor_x, top_y), half_width, cover_pen)
                # Pad the hit rect generously, especially on top: the cover cap
                # sits at the very top of a small box and users aim at it (or a
                # little above), so a tight rect makes the cover feel unhoverable.
                self._hit_rects.append(
                    (
                        dash,
                        QRectF(
                            left_x - 8,
                            top_y - COVER_HOVER_MARGIN_PX,
                            (right_x - left_x) + 16,
                            (floor_y + 8) - (top_y - COVER_HOVER_MARGIN_PX),
                        ),
                    )
                )
                # Cover-cap band (top of the box) → dedicated cover tooltip.
                self._cover_hit_rects.append(
                    (
                        dash,
                        QRectF(
                            left_x - 8,
                            top_y - COVER_HOVER_MARGIN_PX,
                            (right_x - left_x) + 16,
                            COVER_HOVER_MARGIN_PX + 3,
                        ),
                    )
                )
            elif bottom_missing:
                # Cover known, floor missing. Anchor at the pipe invert like a full
                # manhole — chamber from the exaggerated cover down to the invert —
                # and mark the missing floor with an X below, so it stays
                # consistent with neighbours instead of collapsing onto the band.
                invert_pt = (
                    self._plotPointToCanvasPoint(distance, invert_level)
                    if invert_level is not None
                    else None
                )
                if invert_pt is not None:
                    anchor_x = invert_pt.x()
                    top_y = invert_pt.y() - manhole_cover_offset_px(cover_level - invert_level)
                    base_y = invert_pt.y()
                else:
                    anchor_x = cover_pt.x()
                    top_y = base_y = cover_pt.y()

                left_x = anchor_x - half_width
                right_x = anchor_x + half_width
                if base_y > top_y:
                    painter.setPen(shaft_pen)
                    painter.drawLine(QPointF(left_x, top_y), QPointF(left_x, base_y))
                    painter.drawLine(QPointF(right_x, top_y), QPointF(right_x, base_y))
                self._drawCoverCap(painter, QPointF(anchor_x, top_y), half_width, cover_pen)
                x_center = QPointF(anchor_x, base_y + 18.0)
                self._drawMissingDataX(painter, x_center)
                self._drawMissingLabel(painter, x_center, "no bottom level")
                self._hit_rects.append(
                    (
                        dash,
                        QRectF(
                            left_x - 8,
                            top_y - COVER_HOVER_MARGIN_PX,
                            shaft_width_px + 16,
                            (x_center.y() + 10) - (top_y - COVER_HOVER_MARGIN_PX),
                        ),
                    )
                )
                self._cover_hit_rects.append(
                    (
                        dash,
                        QRectF(
                            left_x - 8,
                            top_y - COVER_HOVER_MARGIN_PX,
                            shaft_width_px + 16,
                            COVER_HOVER_MARGIN_PX + 3,
                        ),
                    )
                )
                any_x = True
            else:
                # Bottom known, cover missing. Anchor at the pipe invert — chamber
                # from the invert down to the exaggerated floor — and mark the
                # missing cover with an X above.
                invert_pt = (
                    self._plotPointToCanvasPoint(distance, invert_level)
                    if invert_level is not None
                    else None
                )
                if invert_pt is not None:
                    anchor_x = invert_pt.x()
                    top_y = invert_pt.y()
                    floor_y = invert_pt.y() + manhole_sump_offset_px(invert_level - bottom_level)
                else:
                    anchor_x = bottom_pt.x()
                    top_y = floor_y = bottom_pt.y()

                left_x = anchor_x - half_width
                right_x = anchor_x + half_width
                painter.setPen(shaft_pen)
                if floor_y > top_y:
                    painter.drawLine(QPointF(left_x, top_y), QPointF(left_x, floor_y))
                    painter.drawLine(QPointF(right_x, top_y), QPointF(right_x, floor_y))
                painter.drawLine(QPointF(left_x, floor_y), QPointF(right_x, floor_y))
                x_center = QPointF(anchor_x, top_y - 18.0)
                self._drawMissingDataX(painter, x_center)
                self._drawMissingLabel(painter, x_center, "no cover level")
                self._hit_rects.append(
                    (dash, QRectF(left_x - 4, x_center.y() - 10, shaft_width_px + 8, floor_y - (x_center.y() - 10)))
                )
                any_x = True

        if any_x or any_question:
            self._drawMissingLegend(painter, plot_area, any_x, any_question)

    def _drawReachBands(self, painter, plot_area):
        """
        Draw each reach as a pipe band: the invert polyline anchored at the true
        level, with the soffit offset by an exaggerated pixel thickness along the
        pipe's normal (proportional to clear_height). Offsetting perpendicular to
        the pipe — not straight up — keeps the band a constant visual thickness
        regardless of slope/zoom; a vertical offset looked thin on steep reaches
        and wide on flat ones. Reaches without a clear height fall back to a line.
        """
        if not self._bands:
            return

        invert_color = getattr(self._canvas, "_reach_invert_color", QColor("#1A5276"))
        fill_color = QColor(invert_color)
        fill_color.setAlpha(45)
        # Invert and soffit drawn with the same width so the band reads as a clean
        # pipe (the bottom edge used to look much heavier than the top).
        edge_pen = QPen(invert_color, 1.2)
        edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        invert_pen = edge_pen
        soffit_pen = edge_pen

        # Map plot points to canvas pixels with our own linear transform rather
        # than plotPointToCanvasPoint, which returns nothing for points outside
        # the visible range — that dropped the off-screen end of a partly-visible
        # reach, leaving only the thin QGIS line until the whole reach was panned
        # into view. Off-screen vertices now project and the clip rect trims them.
        mapper = self._plotToCanvasMapper()

        if plot_area is not None and not plot_area.isEmpty():
            painter.save()
            painter.setClipRect(plot_area)

        for band in self._bands:
            invert = band.get("invert") or []
            if len(invert) < 2:
                continue

            invert_pts = self._projectInvert(invert, mapper)
            if len(invert_pts) < 2:
                continue

            band_px = reach_band_px(band.get("clear_height_mm"))
            if band_px is None:
                painter.setPen(invert_pen)
                painter.drawPolyline(QPolygonF(invert_pts))
                continue

            soffit_pts = self._offsetAlongNormal(invert_pts, band_px)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawPolygon(QPolygonF(invert_pts + list(reversed(soffit_pts))))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(invert_pen)
            painter.drawPolyline(QPolygonF(invert_pts))
            painter.setPen(soffit_pen)
            painter.drawPolyline(QPolygonF(soffit_pts))

        if plot_area is not None and not plot_area.isEmpty():
            painter.restore()

    def _plotToCanvasMapper(self):
        """
        Linear (distance, elevation) → canvas-pixel mapper from the visible plot
        ranges and plot area, or None when the canvas can't supply them. Unlike
        plotPointToCanvasPoint it also maps points outside the visible range, so
        a band's off-screen end still projects (the clip rect trims the overflow).
        """
        canvas = self._canvas
        if not (
            hasattr(canvas, "plotArea")
            and hasattr(canvas, "visibleDistanceRange")
            and hasattr(canvas, "visibleElevationRange")
        ):
            return None
        try:
            area = canvas.plotArea()
            dist_range = canvas.visibleDistanceRange()
            elev_range = canvas.visibleElevationRange()
        except Exception:
            return None
        if area is None or area.isEmpty():
            return None
        d_lo, d_hi = dist_range.lower(), dist_range.upper()
        e_lo, e_hi = elev_range.lower(), elev_range.upper()
        if d_hi == d_lo or e_hi == e_lo:
            return None

        left, right = area.left(), area.right()
        top, bottom = area.top(), area.bottom()

        def mapper(distance, elevation):
            fx = (distance - d_lo) / (d_hi - d_lo)
            fy = (elevation - e_lo) / (e_hi - e_lo)
            return QPointF(left + fx * (right - left), bottom - fy * (bottom - top))

        return mapper

    def _projectInvert(self, invert, mapper=None):
        """
        Project (distance, invert_Z) vertices to canvas points. In-range vertices
        use plotPointToCanvasPoint so they stay pixel-aligned with the shafts and
        QGIS line; the mapper is only the fallback for vertices it returns nothing
        for (outside the visible range), so off-screen ends still project.
        """
        points = []
        for distance, z_value in invert:
            pt = self._plotPointToCanvasPoint(distance, z_value)
            if pt is None and mapper is not None:
                pt = mapper(distance, z_value)
            if pt is not None:
                points.append(pt)
        return points

    @staticmethod
    def _offsetAlongNormal(points, offset_px):
        """
        Offset a polyline by ``offset_px`` along its upward normal, so the band
        keeps a constant visual thickness whatever the slope. Vertex normals are
        the averaged normals of the adjacent segments (a simple miter).
        """
        count = len(points)
        seg_normals = []
        for i in range(count - 1):
            dx = points[i + 1].x() - points[i].x()
            dy = points[i + 1].y() - points[i].y()
            length = math.hypot(dx, dy)
            if length == 0:
                seg_normals.append((0.0, -1.0))
                continue
            nx, ny = -dy / length, dx / length
            if ny > 0:  # keep the normal pointing up (toward smaller y / soffit)
                nx, ny = -nx, -ny
            seg_normals.append((nx, ny))

        result = []
        for i in range(count):
            if i == 0:
                nx, ny = seg_normals[0]
            elif i == count - 1:
                nx, ny = seg_normals[-1]
            else:
                ax, ay = seg_normals[i - 1]
                bx, by = seg_normals[i]
                nx, ny = ax + bx, ay + by
                length = math.hypot(nx, ny)
                if length == 0:
                    nx, ny = seg_normals[i]
                else:
                    nx, ny = nx / length, ny / length
            result.append(
                QPointF(points[i].x() + nx * offset_px, points[i].y() + ny * offset_px)
            )
        return result

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

    def _drawNoLevelShaft(self, painter, distance, invert_level, shaft_width_px, plot_area):
        """
        Red dashed shaft + '?' for a manhole with no cover and no bottom level.

        Anchored at the pipe invert (``invert_level``); drawn a fixed pixel
        height upward because the real shaft height is unknown. Returns the
        canvas hit rect when drawn, or None when there is nothing to anchor to.
        """
        if invert_level is None:
            return None
        bottom_pt = self._plotPointToCanvasPoint(distance, invert_level)
        if bottom_pt is None:
            return None
        if plot_area is not None and not plot_area.contains(bottom_pt):
            return None

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
        return QRectF(left, top_y - 4, right - left, bottom_pt.y() - top_y + 8)

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
        self._manhole_shaft_color = QColor("#6E4C1E")  # Brown walls for actual manholes
        self._structure_shaft_color = QColor("#0E6655")  # Teal walls for non-manhole structures
        self._manhole_cover_color = QColor("#2C3E50")  # Dark gray for the cover cap
        self._manhole_chamber_color = QColor("#FFFFFF")  # Opaque chamber interior
        self._reach_invert_color = QColor("#1A5276")  # Pipe band (matches reach line style)
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

    def getManholeDashHitRects(self):
        """(dash, QRectF) pairs in canvas pixels for the structures drawn this frame."""
        if self._manhole_item is None:
            return []
        return self._manhole_item.hitRects()

    def getCoverHitRects(self):
        """(dash, QRectF) pairs in canvas pixels for the cover caps drawn this frame."""
        if self._manhole_item is None:
            return []
        return self._manhole_item.coverHitRects()

    def getReachBands(self):
        """Reach band data (invert vertices + clear_height) for hover gating."""
        if self._manhole_item is None:
            return []
        return self._manhole_item.bands()

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

    def setReachBands(self, bands):
        if self._manhole_item is not None:
            self._manhole_item.setBands(bands or [])
