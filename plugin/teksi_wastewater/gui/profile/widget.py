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

from qgis.core import Qgis, QgsFeatureRequest, QgsGeometry, QgsLineString
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget

from ...tools.twwnetwork import TwwGraphManager
from ...utils.twwlayermanager import TwwLayerManager
from .canvas import TwwElevationProfileCanvas
from .hover_manager import ProfileHoverManager
from .layer_setup import ProfileLayerSetup, _feature_attributes, _pick_attr, _to_float


class TwwElevationProfileWidget(QWidget):
    """
    Widget that wraps QGIS Elevation Profile Canvas for displaying wastewater network profiles.

    Acts as the coordinator between:
    - TwwElevationProfileCanvas (rendering)
    - ProfileLayerSetup (data source configuration)
    - ProfileHoverManager (hover, tooltip, map highlight)

    This widget replaces the old TwwPlotSVGWidget which used QtWebKit.
    """

    def __init__(self, parent, network_analyzer: TwwGraphManager = None):
        """
        Initialize the elevation profile widget.

        :param parent: Parent widget.
        :param network_analyzer: Network analyzer instance (kept for compatibility).
        """
        QWidget.__init__(self, parent)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Canvas (custom subclass)
        self.canvas = TwwElevationProfileCanvas(
            self, self._onCanvasMouseMove, self._onCanvasLeave
        )
        layout.addWidget(self.canvas)

        # Compatibility / misc state
        self.networkAnalyzer = network_analyzer
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

    # ------------------------------------------------------------------
    # Canvas event routing
    # ------------------------------------------------------------------

    def _onCanvasMouseMove(self, event):
        self._hover_manager.onCanvasMouseMove(event)

    def _onCanvasLeave(self, event):
        self._hover_manager.onCanvasLeave(event)

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
        if hasattr(self.canvas, "cancelJobs"):
            self.canvas.cancelJobs()

        self._hover_manager.clearState()
        self.canvas.setManholeDashes([])
        self._profile_curve_geom = None

        # Note: canvas.clear() crashes QGIS; use empty curve + refresh instead
        empty_curve = QgsLineString()
        self.canvas.setProfileCurve(empty_curve)

        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.refresh()

    def setProfileCurve(self, geometry):
        """
        Set the profile curve (path) for the elevation profile.

        :param geometry: QgsGeometry object representing the path.
        """
        if not isinstance(geometry, QgsGeometry) or geometry.isEmpty():
            return
        points = geometry.asPolyline()
        if not points:
            return

        # Clean up old state before setting new profile
        self._hover_manager.clearState()
        self.canvas.setManholeDashes([])

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

        if hasattr(self.canvas, "cancelJobs"):
            self.canvas.cancelJobs()
        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.setProfileCurve(curve)
        self.canvas.refresh()

        self._profile_curve_geom = geometry
        self._refreshManholeDashes()

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
                        self.canvas.setVisiblePlotRange(d_min, d_max, e_min, e_max)
                        self.canvas.refresh()
                except Exception:
                    pass

        # Delay to allow canvas to finish processing current event
        QTimer.singleShot(0, delayedZoomFull)

    def setProfileFromTree(self, nodes, edges):
        """
        Set the profile curve from tree data (nodes and edges).

        Builds a polyline geometry from the edge list and calls setProfileCurve().

        :param nodes: List of nodes (kept for API compatibility, not used directly).
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
    # Manhole dashes (profile data construction, stays in widget)
    # ------------------------------------------------------------------

    def _refreshManholeDashes(self):
        """Build vertical shaft data (cover → bottom) for manholes along the profile curve."""
        if self._profile_curve_geom is None or self._profile_curve_geom.isEmpty():
            self.canvas.setManholeDashes([])
            return

        # Single source: vw_tww_wastewater_structure. Its geometry is the main
        # wastewater node position (COALESCE(wn.situation3d_geometry, main
        # cover)), so it sits exactly on the profile curve and replaces the
        # former vw_wastewater_node scan entirely. Levels come from co_level /
        # wn_bottom_level with deliberately NO fallback — a missing level is
        # flagged and rendered as a red X so the data gap stays visible.
        ws_layer = TwwLayerManager.layer("vw_tww_wastewater_structure")
        if ws_layer is None:
            self.canvas.setManholeDashes([])
            return

        dashes = []
        for feature in ws_layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            attrs = _feature_attributes(feature)

            cover_level = _to_float(_pick_attr(attrs, ["co_level"]))
            bottom_level = _to_float(_pick_attr(attrs, ["wn_bottom_level"]))
            cover_level_missing = cover_level is None
            bottom_level_missing = bottom_level is None or bottom_level == 0
            if bottom_level_missing:
                bottom_level = None
            # Both levels missing: no elevation to anchor a marker to.
            if cover_level_missing and bottom_level_missing:
                continue

            if geometry.type() != Qgis.GeometryType.Point:
                try:
                    geometry = geometry.centroid()
                except Exception:
                    continue

            try:
                distance_along = self._profile_curve_geom.lineLocatePoint(geometry)
            except Exception:
                continue

            if distance_along is None or distance_along < 0:
                continue

            try:
                if self._profile_curve_geom.distance(geometry) > self._manhole_dash_tolerance:
                    continue
            except Exception:
                pass

            dim1_mm = _to_float(_pick_attr(attrs, ["ma_dimension1"]))
            line_width = self._manholeDashWidth(dim1_mm)
            dashes.append(
                {
                    "distance": float(distance_along),
                    # Keep the node obj_id (wn_obj_id) — downstream hover code
                    # treats dash obj_id as a wastewater node id.
                    "obj_id": _pick_attr(attrs, ["wn_obj_id"]),
                    "cover_level": cover_level,
                    "bottom_level": bottom_level,
                    "cover_level_missing": cover_level_missing,
                    "bottom_level_missing": bottom_level_missing,
                    "width": line_width,
                }
            )

        self.canvas.setManholeDashes(dashes)

    def _manholeDashWidth(self, dim1_mm):
        """
        Calculate the pixel width for a manhole shaft.

        :param dim1_mm: Shaft diameter / first dimension in millimetres
            (from vw_tww_wastewater_structure.ma_dimension1). None when the
            structure has no recorded dimension (e.g. special_structure).
        :return: Line width in pixels (mm/100, clamped 6–16px). Falls back to
            canvas default (~10px) when dim1_mm is missing.

        TODO: switch to true-scale rendering once Pipe Band lands —
            half_px = (dim1_mm / 1000.0) * px_per_m / 2.0, clamped >= 2px.
            See profile/reference.md §5.2.
        """
        default_px = (
            self.canvas._manhole_default_px_width
            if hasattr(self.canvas, "_manhole_default_px_width")
            else 10
        )
        if dim1_mm is None:
            return default_px
        return max(6.0, min(16.0, float(dim1_mm) / 100.0))
