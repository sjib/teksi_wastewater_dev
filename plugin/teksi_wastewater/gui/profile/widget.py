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

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsLineString
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget

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
        if hasattr(self.canvas, "setVerticalExaggeration"):
            self.canvas.setVerticalExaggeration(self.verticalExaggeration)
        self._applyVerticalExaggeration()

    def _veElevationSpan(self, dist_len):
        """
        Elevation span (m) to show so the vertical scale equals the horizontal
        scale × the current V.E. The profile is drawn at TRUE scale, so the V.E.
        is the only vertical magnification — a uniform viewing transform, not a
        per-feature distortion. Returns None when the canvas can't supply sizes.
        """
        ve = getattr(self, "verticalExaggeration", 1.0) or 1.0
        if ve <= 0 or dist_len <= 0 or not hasattr(self.canvas, "plotArea"):
            return None
        try:
            area = self.canvas.plotArea()
        except Exception:
            return None
        if area is None or area.isEmpty():
            return None
        plot_w = area.width()
        plot_h = area.height()
        if plot_w <= 0 or plot_h <= 0:
            return None
        horizontal_scale = plot_w / dist_len  # px per metre
        vertical_scale = horizontal_scale * ve
        return plot_h / vertical_scale  # elevation metres shown

    def _applyVerticalExaggeration(self):
        """Re-set the visible elevation range to honour the current V.E."""
        canvas = self.canvas
        if not (
            hasattr(canvas, "visibleDistanceRange")
            and hasattr(canvas, "visibleElevationRange")
            and hasattr(canvas, "setVisiblePlotRange")
        ):
            return
        try:
            dist_range = canvas.visibleDistanceRange()
            elev_range = canvas.visibleElevationRange()
        except Exception:
            return
        dist_len = dist_range.upper() - dist_range.lower()
        span = self._veElevationSpan(dist_len)
        if span is None or span <= 0:
            return
        e_center = (elev_range.lower() + elev_range.upper()) / 2.0
        try:
            canvas.setVisiblePlotRange(
                dist_range.lower(), dist_range.upper(),
                e_center - span / 2.0, e_center + span / 2.0,
            )
            canvas.refresh()
        except Exception:
            pass

    def printProfile(self):
        """
        Print the profile to PDF.

        TODO: Implement — render the canvas to an image/PDF.
        """
        pass

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

        def delayedZoomFull():
            if hasattr(self.canvas, "zoomFull"):
                try:
                    self.canvas.zoomFull()
                    if (
                        hasattr(self.canvas, "visibleDistanceRange")
                        and hasattr(self.canvas, "visibleElevationRange")
                        and hasattr(self.canvas, "setVisiblePlotRange")
                    ):
                        dist_range = self.canvas.visibleDistanceRange()
                        elev_range = self.canvas.visibleElevationRange()
                        dist_len = dist_range.upper() - dist_range.lower()
                        elev_len = elev_range.upper() - elev_range.lower()
                        margin_dist = max(dist_len * 0.05, 1.0)
                        margin_elev = max(elev_len * 0.05, 0.5)
                        d_min = dist_range.lower() - margin_dist
                        d_max = dist_range.upper() + margin_dist
                        e_min = elev_range.lower() - margin_elev
                        e_max = elev_range.upper() + margin_elev
                        # Initial view fits the whole profile (never clips the
                        # relief); the V.E. slider zooms the vertical from here.
                        self.canvas.setVisiblePlotRange(d_min, d_max, e_min, e_max)
                        self.canvas.refresh()
                except Exception:
                    pass

        # Delay to allow canvas to finish processing current event
        QTimer.singleShot(0, delayedZoomFull)

    def setProfileFromTree(self, edges):
        """
        Set the profile curve from tree data (edges).

        Builds a polyline geometry from the edge list and calls setProfileCurve().

        :param edges: List of (from_node, to_node, edge_info) tuples.
        """
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer or not edges:
            return

        reach_ids = []
        for item in edges:
            item_info = item[2]
            if item_info.get("objType") == "reach":
                base_feature = item_info.get("baseFeature")
                if base_feature:
                    reach_ids.append(base_feature)

        if not reach_ids:
            return

        reach_list = ",".join("'" + rid + "'" for rid in reach_ids if rid)
        request = QgsFeatureRequest()
        request.setFilterExpression(f"obj_id IN ({reach_list})")

        points = []
        for feature in reach_layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry:
                polyline = geometry.asPolyline()
                if points:
                    if points[-1] == polyline[0]:
                        points.extend(polyline[1:])
                    else:
                        points.extend(polyline)
                else:
                    points.extend(polyline)

        if points:
            profile_geometry = QgsGeometry.fromPolylineXY(points)
            self.setProfileCurve(profile_geometry)

    # ------------------------------------------------------------------
    # Canvas helpers
    # ------------------------------------------------------------------

    def _cancelCanvasJobs(self):
        if hasattr(self.canvas, "cancelJobs"):
            self.canvas.cancelJobs()

    def _invalidateAndRefreshCanvas(self):
        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.refresh()
