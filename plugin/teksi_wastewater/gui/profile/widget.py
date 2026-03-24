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
        self._profile_generation_complete = False

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

        # Monitor profile generation completion
        if hasattr(self.canvas, "activeJobCountChanged"):
            self.canvas.activeJobCountChanged.connect(self._onJobCountChanged)

    # ------------------------------------------------------------------
    # Canvas event routing
    # ------------------------------------------------------------------

    def _onCanvasMouseMove(self, event):
        self._hover_manager.onCanvasMouseMove(event)

    def _onCanvasLeave(self, event):
        self._hover_manager.onCanvasLeave(event)

    def _onJobCountChanged(self, count):
        """Called when profile generation jobs change; marks generation complete at 0."""
        if count == 0 and not self._profile_generation_complete:
            self._profile_generation_complete = True

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
        self._profile_generation_complete = False
        self._profile_curve_geom = None

        # Note: canvas.clear() crashes QGIS; use empty curve + refresh instead
        empty_curve = QgsLineString()
        self.canvas.setProfileCurve(empty_curve)

        if hasattr(self.canvas, "invalidateCurrentPlotExtent"):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.refresh()

    def setupDataSources(self):
        """
        Set up data sources for the elevation profile canvas.

        Delegates to ProfileLayerSetup.setup().
        """
        self._layer_setup.setup(tolerance=self._manhole_dash_tolerance)

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
        self._profile_generation_complete = False

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

        layer = TwwLayerManager.layer("vw_wastewater_node")
        if layer is None:
            self.canvas.setManholeDashes([])
            return

        cover_layer = TwwLayerManager.layer("vw_cover") or TwwLayerManager.layer("vm_cover")

        # Build cover lookup: ws_id → {cover_level, geometry}
        cover_by_ws = {}
        if cover_layer is not None:
            for cover_feature in cover_layer.getFeatures():
                cover_attrs = _feature_attributes(cover_feature)
                ws_id = _pick_attr(
                    cover_attrs,
                    [
                        "fk_wastewater_structure",
                        "fk_wastewater_structure_obj_id",
                        "ws_obj_id",
                        "fk_wastewater_structure_id",
                    ],
                )
                if not ws_id:
                    continue
                cover_level = _to_float(
                    _pick_attr(cover_attrs, ["cover_level", "level", "elevation", "z"])
                )
                if cover_level is None:
                    cover_geom = cover_feature.geometry()
                    if cover_geom and not cover_geom.isEmpty():
                        cover_level = self._pointZ(cover_geom.asPoint())
                if cover_level is None:
                    continue
                cover_by_ws[str(ws_id)] = {
                    "cover_level": cover_level,
                    "geometry": cover_feature.geometry(),
                }

        # Build reach-level lookup: node_obj_id → [connected reach levels]
        reach_levels_by_node = {}
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if reach_layer is not None:
            for reach_feat in reach_layer.getFeatures():
                reach_attrs = _feature_attributes(reach_feat)
                to_node_id = _pick_attr(
                    reach_attrs,
                    [
                        "rp_to_fk_wastewater_networkelement",
                        "rp_to_fk_wastewater_networkelement_id",
                    ],
                )
                to_level = _to_float(_pick_attr(reach_attrs, ["rp_to_level"]))
                if to_node_id and to_level is not None:
                    reach_levels_by_node.setdefault(str(to_node_id), []).append(to_level)

                from_node_id = _pick_attr(
                    reach_attrs,
                    [
                        "rp_from_fk_wastewater_networkelement",
                        "rp_from_fk_wastewater_networkelement_id",
                    ],
                )
                from_level = _to_float(_pick_attr(reach_attrs, ["rp_from_level"]))
                if from_node_id and from_level is not None:
                    reach_levels_by_node.setdefault(str(from_node_id), []).append(from_level)

        from qgis.core import Qgis

        dashes = []
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            attrs = _feature_attributes(feature)
            ws_id = _pick_attr(
                attrs,
                [
                    "fk_wastewater_structure",
                    "fk_wastewater_structure_obj_id",
                    "ws_obj_id",
                    "fk_wastewater_structure_id",
                ],
            )

            cover_level = None
            cover_geometry = None
            if ws_id:
                cover_data = cover_by_ws.get(str(ws_id))
                if cover_data:
                    cover_level = cover_data.get("cover_level")
                    cover_geometry = cover_data.get("geometry")

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

            if cover_level is None:
                cover_level = _to_float(_pick_attr(attrs, ["cover_level", "coverLevel"]))
            if cover_level is None and cover_geometry is not None and not cover_geometry.isEmpty():
                cover_level = self._pointZ(cover_geometry.asPoint())

            bottom_level = _to_float(
                _pick_attr(
                    attrs,
                    [
                        "bottom_level",
                        "bottomLevel",
                        "invert_level",
                        "level",
                        "backflow_level",
                        "backflow_level_current",
                    ],
                )
            )

            bottom_level_missing = False
            if bottom_level is None or bottom_level == 0:
                node_obj_id = _pick_attr(attrs, ["obj_id", "objId", "id"])
                fallback_level = None
                if node_obj_id:
                    connected_levels = reach_levels_by_node.get(str(node_obj_id), [])
                    if connected_levels:
                        fallback_level = min(connected_levels)
                if fallback_level is not None:
                    bottom_level = fallback_level
                    bottom_level_missing = True
                else:
                    continue

            if cover_level is None or bottom_level is None:
                continue

            line_width = self._manholeDashWidth(attrs)
            obj_id = _pick_attr(attrs, ["obj_id", "objId", "id"])
            dashes.append(
                {
                    "distance": float(distance_along),
                    "cover_level": cover_level,
                    "bottom_level": bottom_level,
                    "bottom_level_missing": bottom_level_missing,
                    "width": line_width,
                    "obj_id": obj_id,
                    "color": "#6E4C1E",
                }
            )

        self.canvas.setManholeDashes(dashes)

    def _manholeDashWidth(self, attrs):
        """
        Calculate the pixel width for a manhole shaft based on its diameter.

        TODO: Get actual diameter from DB fields (dimension1) when available.
        Currently falls back to default 10px if no diameter is found.

        :param attrs: Feature attributes dict.
        :return: Line width in pixels (scaled from diameter_mm, clamped 6–16px).
        """
        diameter = _to_float(
            _pick_attr(
                attrs,
                [
                    "dimension1",
                    "dimension_1",
                    "diameter",
                    "manhole_diameter",
                    "diameter_mm",
                    "width",
                    "clear_height",
                ],
            )
        )

        default_px = (
            self.canvas._manhole_default_px_width
            if hasattr(self.canvas, "_manhole_default_px_width")
            else 10
        )

        if diameter is None:
            return default_px

        # Convert meters to mm if value appears to be in meters (< 10)
        diameter_mm = diameter * 1000.0 if diameter < 10 else diameter
        width = diameter_mm / 100.0
        return max(6.0, min(16.0, float(width)))

    # ------------------------------------------------------------------
    # Geometry Z helper (used by _refreshManholeDashes)
    # ------------------------------------------------------------------

    def _pointZ(self, point):
        if point is None:
            return None
        if hasattr(point, "z"):
            try:
                z_value = point.z() if callable(point.z) else point.z
                return _to_float(z_value)
            except Exception:
                return None
        return None
