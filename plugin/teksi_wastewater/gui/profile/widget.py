# -----------------------------------------------------------
#
# Elevation Profile Widget — Main Coordinator
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

from datetime import date

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsLineString
from qgis.PyQt.QtCore import QRectF, QTimer
from qgis.PyQt.QtGui import QFont, QPageLayout, QPageSize, QPainter
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget

from ...utils.twwlayermanager import TwwLayerManager
from .canvas import TwwElevationProfileCanvas
from .hover_manager import ProfileHoverManager
from .layer_setup import ProfileLayerSetup


class TwwElevationProfileWidget(QWidget):
    """
    Widget that wraps QGIS Elevation Profile Canvas for displaying wastewater network profiles.

    Acts as the coordinator between:
    - TwwElevationProfileCanvas (rendering)
    - ProfileLayerSetup (data source configuration)
    - ProfileHoverManager (hover, tooltip, map highlight)

    This widget replaces the old TwwPlotSVGWidget which used QtWebKit.
    """

    def __init__(self, parent):
        """
        Initialize the elevation profile widget.

        :param parent: Parent widget.
        """
        QWidget.__init__(self, parent)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Canvas (custom subclass)
        self.canvas = TwwElevationProfileCanvas(self)
        layout.addWidget(self.canvas)

        # Misc state
        self.verticalExaggeration = 10.0
        self._data_sources_setup = False
        self._profile_curve_geom = None
        self._manhole_dash_tolerance = 10.0

        # Initial padded zoom is applied when the async profile generation
        # finishes (see _onActiveJobCountChanged), not on a 0ms timer: the
        # canvas auto-zoomFulls when the job completes (because of
        # invalidateCurrentPlotExtent), which would overwrite any visible
        # range set earlier.
        self._initial_zoom_pending = False
        self._initial_zoom_retries = 0
        self._job_signal_connected = False
        if hasattr(self.canvas, "activeJobCountChanged"):
            try:
                self.canvas.activeJobCountChanged.connect(self._onActiveJobCountChanged)
                self._job_signal_connected = True
            except Exception:
                pass

        # Layer setup helper (owns temp memory layers)
        self._layer_setup = ProfileLayerSetup(self.canvas)

        # Hover / tooltip / highlight helper
        try:
            from qgis.utils import iface as _iface

            map_canvas = _iface.mapCanvas() if _iface else None
        except Exception:
            map_canvas = None

        self._hover_manager = ProfileHoverManager(self.canvas, map_canvas)
        self._hover_manager.setup()
        self.canvas.setHoverHandlers(
            self._hover_manager.onCanvasMouseMove,
            self._hover_manager.onCanvasLeave,
        )

    # ------------------------------------------------------------------
    # Public API (called by TwwProfileDockWidget)
    # ------------------------------------------------------------------

    def changeVerticalExaggeration(self, val):
        """
        Change the vertical exaggeration of the profile.

        :param val: Vertical exaggeration value (e.g., 10 for 10x).
        """
        self.verticalExaggeration = float(val)
        # TODO: Apply vertical exaggeration to canvas
        # Note: QgsElevationProfileCanvas uses axisScaleRatio() which is read-only

    def printProfile(self):
        """
        Print the current profile via a system print-preview dialog.

        The canvas is grabbed as an image and scaled onto an A4 landscape
        page: the manhole shafts, pipe bands and missing-data marks are
        custom scene items, so a canvas grab is the only rendering path that
        includes them (a layout-item / vector export would drop them all).
        A PDF file can be produced by picking a PDF printer in the preview.
        """
        pixmap = self._grabProfilePixmap(self.tr("Print profile"))
        if pixmap is None:
            return

        # QtPrintSupport can be missing from stripped-down builds; degrade
        # with a message instead of crashing (defensive-style, see CLAUDE.md).
        try:
            from qgis.PyQt.QtPrintSupport import QPrinter, QPrintPreviewDialog
        except ImportError:
            QMessageBox.warning(
                self,
                self.tr("Print profile"),
                self.tr("Qt print support is not available in this QGIS build."),
            )
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        # Qt6 removed QPrinter.setPaperSize()/setOrientation(); QPageSize and
        # QPageLayout work on Qt5 and Qt6 alike (fully-scoped enum access
        # resolves under both PyQt5 and PyQt6).
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)

        dialog = QPrintPreviewDialog(printer, self)
        dialog.paintRequested.connect(
            lambda p, pm=pixmap: self._paintProfilePage(p, pm)
        )
        dialog.exec()

    def exportProfileImage(self):
        """
        Export the current profile canvas as an image file (PNG/JPEG).

        Same rendering path as printProfile — a canvas grab is the only way
        to include the custom overlay items. The image is written at the
        grabbed resolution (device-pixel-ratio aware, so 2x on HiDPI).
        """
        pixmap = self._grabProfilePixmap(self.tr("Export profile image"))
        if pixmap is None:
            return

        default_name = f"tww_profile_{date.today().isoformat()}.png"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Export profile image"),
            default_name,
            self.tr("PNG image (*.png);;JPEG image (*.jpg)"),
        )
        if not path:
            return
        # No extension typed: derive it from the chosen filter (pixmap.save
        # picks the format from the suffix).
        if "." not in path.split("/")[-1].split("\\")[-1]:
            path += ".jpg" if "*.jpg" in selected_filter else ".png"

        if not pixmap.save(path):
            QMessageBox.warning(
                self,
                self.tr("Export profile image"),
                self.tr("Could not write the image to:") + f"\n{path}",
            )

    def _grabProfilePixmap(self, dialog_title):
        """
        Guard + hover cleanup + canvas grab shared by print and image export.
        Returns the grabbed QPixmap, or None (after informing the user) when
        there is no profile to render.
        """
        if self._profile_curve_geom is None:
            QMessageBox.information(
                self,
                dialog_title,
                self.tr("No profile is currently displayed."),
            )
            return None
        # Drop hover artefacts (map highlight, QGIS's own hover cross hairs)
        # so they don't end up in the grabbed image.
        self._hover_manager.clearState()
        pixmap = self.canvas.grab()
        return None if pixmap.isNull() else pixmap

    def _paintProfilePage(self, printer, pixmap):
        """
        Paint the grabbed profile pixmap onto one printer page.

        Called from QPrintPreviewDialog.paintRequested — possibly several
        times (preview refreshes, then the real print run, each with its own
        resolution), so the page geometry is recomputed on every call.
        """
        from qgis.PyQt.QtPrintSupport import QPrinter

        painter = QPainter(printer)
        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            # QPainter on a QPrinter has its origin at the printable area's
            # top-left, so only the page SIZE matters here.
            page_w, page_h = page.width(), page.height()

            # Header line; point-based font sizes are device-independent.
            font = QFont(self.font())
            font.setPointSize(10)
            painter.setFont(font)
            header = self.tr("TEKSI wastewater — length profile") + f"  ({date.today().isoformat()})"
            metrics = painter.fontMetrics()
            painter.drawText(QRectF(0, 0, page_w, metrics.height() * 1.5), header)
            header_h = metrics.height() * 2.0

            # Fit the pixmap into the remaining page, centred, aspect kept
            # (the device-pixel-ratio cancels out of the aspect ratio).
            avail_w = page_w
            avail_h = page_h - header_h
            if avail_w <= 0 or avail_h <= 0 or pixmap.height() == 0:
                return
            ratio = pixmap.width() / pixmap.height()
            if avail_w / avail_h > ratio:
                target_h = avail_h
                target_w = avail_h * ratio
            else:
                target_w = avail_w
                target_h = avail_w / ratio
            target = QRectF(
                (avail_w - target_w) / 2.0,
                header_h + (avail_h - target_h) / 2.0,
                target_w,
                target_h,
            )
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        finally:
            painter.end()

    def clearProfile(self):
        """
        Clear the profile canvas completely.

        Called by TwwProfileDockWidget when the user clicks the Clear Canvas button.
        Safely clears all profile-related state without crashing.
        """
        self._cancelCanvasJobs()

        self._hover_manager.clearState()
        self.canvas.setManholeDashes([])
        self.canvas.setReachBands([])
        self.canvas.setChangePoints([])
        self._profile_curve_geom = None

        # Note: canvas.clear() crashes QGIS; use empty curve + refresh instead
        empty_curve = QgsLineString()
        self.canvas.setProfileCurve(empty_curve)

        self._invalidateAndRefreshCanvas()

    def clearHighlight(self):
        """Clear hover tooltip and map highlight (e.g. when dock closes)."""
        self._hover_manager.clearState()

    def setProfileCurve(self, geometry, reach_ids=None, node_points=None):
        """
        Set the profile curve (path) for the elevation profile.

        :param geometry: QgsGeometry object representing the path.
        :param reach_ids: optional iterable of selected reach obj_ids. When
            given, only these reaches are rendered (keeps side branches that
            share a node with the path out of the profile).
        :param node_points: optional iterable of QgsPointXY of the selected
            path's nodes, used to filter structures the same way.
        """
        if not isinstance(geometry, QgsGeometry) or geometry.isEmpty():
            return
        points = geometry.asPolyline()
        if not points:
            return

        # Clean up old state before setting new profile
        self._hover_manager.clearState()

        curve = QgsLineString(points)

        # Set up data sources only on the first call.
        # NOTE: Do NOT re-call setupDataSources() — calling setLayers() on an
        # active canvas causes access violation in QgsElevationProfileCanvas.
        layers_configured = False
        if hasattr(self.canvas, "layers"):
            layers = self.canvas.layers()
            layers_configured = bool(layers and len(layers) > 0)

        if not layers_configured or not self._data_sources_setup:
            self._layer_setup.setup(tolerance=self._manhole_dash_tolerance)
            self._data_sources_setup = True
        else:
            self._layer_setup.applyCanvasTolerance(self._manhole_dash_tolerance)

        # Restrict the rendered features to the selected path (no-op when the
        # caller passes no selection, e.g. the tree tool).
        self._layer_setup.updatePathFeatures(reach_ids=reach_ids, node_points=node_points)

        self._cancelCanvasJobs()
        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.setProfileCurve(curve)
        self.canvas.refresh()

        self._profile_curve_geom = geometry
        self.canvas.setManholeDashes(
            self._layer_setup.buildManholeDashes(
                self._profile_curve_geom,
                self._manhole_dash_tolerance,
                self.canvas.manholeDefaultPxWidth(),
            )
        )
        self.canvas.setReachBands(
            self._layer_setup.buildReachBands(
                self._profile_curve_geom,
                self._manhole_dash_tolerance,
            )
        )
        self.canvas.setChangePoints(
            self._layer_setup.buildChangePointMarkers(
                self._profile_curve_geom,
                self._manhole_dash_tolerance,
            )
        )

        # Apply the padded zoom once the async profile generation is done; the
        # 0ms fallback (no job signal on this QGIS) keeps the old behaviour.
        self._initial_zoom_pending = True
        self._initial_zoom_retries = 0
        if not self._job_signal_connected:
            QTimer.singleShot(0, self._applyPaddedZoomFull)

    def setProfileFromTree(self, edges):
        """
        Set the profile curve from an upstream/downstream trace.

        The trace (``edges``) is a tree rooted at the clicked node (see
        TwwGraph.getTree). A length profile is a single 1-D path, so a branching
        tree cannot be drawn as-is without pipes crossing at junctions. We
        therefore reduce the tree to its **main trunk**: the path from the root
        to the farthest node (by cumulative reach length), and render only that.
        Reaches are oriented to the running path and their shared junction
        vertices de-duplicated so the curve never zig-zags; the trunk's reach
        ids and node points are forwarded so only this path is drawn.

        :param edges: List of (parent_node, child_node, edge_info) tuples, where
            edge_info carries ``weight`` (length), ``objType`` and
            ``baseFeature`` (reach obj_id). Edges point away from the root.
        """
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer or not edges:
            return

        # --- Reduce the traced tree to its longest root->leaf trunk ----------
        # Edges point parent->child, away from the root (the clicked node).
        children = {}
        parent_of = {}
        for parent, child, info in edges:
            children.setdefault(parent, []).append((child, info))
            parent_of[child] = (parent, info)

        # The root is the only node that is never a child.
        roots = [p for p in children if p not in parent_of]
        if not roots:
            return
        root = roots[0]

        # Farthest node from the root by cumulative length (tree => a leaf).
        far_node, far_dist = root, 0.0
        stack = [(root, 0.0)]
        while stack:
            node, dist = stack.pop()
            if dist > far_dist:
                far_node, far_dist = node, dist
            for child, info in children.get(node, []):
                stack.append((child, dist + (info.get("weight") or 0.0)))

        # Walk root <- ... <- far_node to collect the trunk edges in path order.
        trunk = []
        node = far_node
        while node in parent_of:
            parent, info = parent_of[node]
            trunk.append(info)
            node = parent
        trunk.reverse()

        # Reach obj_ids along the trunk, in order, de-duplicating a reach that
        # spans several routing segments (consecutive identical baseFeature).
        reach_ids = []
        for info in trunk:
            if info.get("objType") == "reach":
                base_feature = info.get("baseFeature")
                if base_feature and (not reach_ids or reach_ids[-1] != base_feature):
                    reach_ids.append(base_feature)

        if not reach_ids:
            return

        # --- Build the oriented profile curve from the trunk reaches ---------
        reach_list = ",".join("'" + rid + "'" for rid in reach_ids)
        request = QgsFeatureRequest()
        request.setFilterExpression(f"obj_id IN ({reach_list})")
        polyline_by_id = {}
        for feature in reach_layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            polyline = geometry.asPolyline()
            if polyline:
                polyline_by_id[feature["obj_id"]] = polyline

        ordered = [list(polyline_by_id[rid]) for rid in reach_ids if rid in polyline_by_id]
        if not ordered:
            return

        # Orient the first reach against the second so the chain connects
        # head-to-tail; the loop then orients each remaining reach to the path.
        if len(ordered) >= 2:
            first, nxt = ordered[0], ordered[1]
            nxt_ends = (nxt[0], nxt[-1])
            if min(first[0].sqrDist(p) for p in nxt_ends) < min(
                first[-1].sqrDist(p) for p in nxt_ends
            ):
                first.reverse()

        points = []
        node_points = []
        for polyline in ordered:
            if points:
                tail = points[-1]
                if tail.sqrDist(polyline[-1]) < tail.sqrDist(polyline[0]):
                    polyline.reverse()
            # Reach endpoints are the path's node positions (used to keep only
            # the structures sitting on this path).
            node_points.append(polyline[0])
            node_points.append(polyline[-1])
            if points and points[-1].sqrDist(polyline[0]) < 1e-6:
                polyline = polyline[1:]
            points.extend(polyline)

        if len(points) >= 2:
            profile_geometry = QgsGeometry.fromPolylineXY(points)
            self.setProfileCurve(
                profile_geometry, reach_ids=reach_ids, node_points=node_points
            )

    # ------------------------------------------------------------------
    # Canvas helpers
    # ------------------------------------------------------------------

    def _onActiveJobCountChanged(self, count):
        if count == 0 and self._initial_zoom_pending:
            self._initial_zoom_pending = False
            # Defer one event-loop turn so this runs AFTER the canvas's own
            # job-finished handling — invalidateCurrentPlotExtent makes it
            # auto-zoomFull there, which would overwrite our padded range.
            QTimer.singleShot(0, self._applyPaddedZoomFull)

    def _applyPaddedZoomFull(self):
        """
        zoomFull, then widen the visible range so the manhole overlay fits.

        zoomFull only knows the layer features (true levels), but the manhole
        overlay draws in exaggerated pixel space around each pipe invert —
        without extra headroom the topmost cover gets clamped against the plot
        edge. The overlay's pixel extents are converted to data units and the
        margins widened to fit them; iterated, because widening the range
        changes the px-per-unit scale the conversion depends on.
        """
        self._initial_zoom_pending = False
        if self._profile_curve_geom is None or not hasattr(self.canvas, "zoomFull"):
            return
        try:
            self.canvas.zoomFull()
            if not (
                hasattr(self.canvas, "visibleDistanceRange")
                and hasattr(self.canvas, "visibleElevationRange")
                and hasattr(self.canvas, "setVisiblePlotRange")
            ):
                return
            dist_range = self.canvas.visibleDistanceRange()
            elev_range = self.canvas.visibleElevationRange()
            d_lo, d_hi = dist_range.lower(), dist_range.upper()
            e_lo, e_hi = elev_range.lower(), elev_range.upper()
            dist_len = d_hi - d_lo
            elev_len = e_hi - e_lo
            base_dist = max(dist_len * 0.05, 1.0)
            base_elev = max(elev_len * 0.05, 0.5)
            margin_left = margin_right = base_dist
            margin_top = margin_bottom = base_elev

            extents = (
                self.canvas.manholeDashExtentsPx()
                if hasattr(self.canvas, "manholeDashExtentsPx")
                else []
            )
            area = self.canvas.plotArea() if hasattr(self.canvas, "plotArea") else None
            if extents and (area is None or area.isEmpty()) and self._initial_zoom_retries < 5:
                # Plot area not laid out yet (first render still pending) — the
                # px→data conversion needs it, so try again shortly.
                self._initial_zoom_retries += 1
                QTimer.singleShot(120, self._applyPaddedZoomFull)
                return

            if (
                extents
                and area is not None
                and not area.isEmpty()
                and elev_len > 0
                and dist_len > 0
            ):
                for _ in range(3):
                    px_per_m = area.height() / (elev_len + margin_top + margin_bottom)
                    px_per_d = area.width() / (dist_len + margin_left + margin_right)
                    margin_top = margin_bottom = base_elev
                    margin_left = margin_right = base_dist
                    for dist, anchor, up_px, down_px, half_w_px in extents:
                        margin_top = max(margin_top, up_px / px_per_m - (e_hi - anchor))
                        margin_bottom = max(
                            margin_bottom, down_px / px_per_m - (anchor - e_lo)
                        )
                        margin_left = max(
                            margin_left, half_w_px / px_per_d - (dist - d_lo)
                        )
                        margin_right = max(
                            margin_right, half_w_px / px_per_d - (d_hi - dist)
                        )

            self.canvas.setVisiblePlotRange(
                d_lo - margin_left,
                d_hi + margin_right,
                e_lo - margin_bottom,
                e_hi + margin_top,
            )
            self.canvas.refresh()
        except Exception:
            pass

    def _cancelCanvasJobs(self):
        if hasattr(self.canvas, "cancelJobs"):
            self.canvas.cancelJobs()

    def _invalidateAndRefreshCanvas(self):
        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.refresh()
