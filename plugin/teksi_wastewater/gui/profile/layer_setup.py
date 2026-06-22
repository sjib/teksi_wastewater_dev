# -----------------------------------------------------------
#
# Elevation Profile — Layer Setup
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

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsSimpleLineSymbolLayer,
    QgsVectorLayer,
    QgsVectorLayerElevationProperties,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ...utils.database_utils import DatabaseUtils
from ...utils.twwlayermanager import TwwLayerManager

# Narrowest schematic shaft width (px); also the fallback when ma_dimension1 is
# missing, so a manhole with no known width reads as the thinnest, not the widest.
MANHOLE_DEFAULT_PX_WIDTH = 14

# Schematic manhole shaft height (px). Real shaft depth (2-10 m) is sub-pixel
# once a whole network's relief is in view, so the height is exaggerated and
# proportional — clamped to this range — while the bottom stays at the true level.
MANHOLE_MIN_SHAFT_PX = 12.0
MANHOLE_MAX_SHAFT_PX = 70.0

# The shaft is anchored at the pipe invert (rp level) and split into two
# exaggerated segments: invert->cover (up) and invert->floor / sump (down).
# Same px-per-metre gain for both so their ratio stays truthful; small minimums
# keep the cover clearly above and the floor clearly below the pipe.
MANHOLE_VERTICAL_GAIN_PX = 6.0
MANHOLE_MIN_COVER_PX = 10.0
MANHOLE_MIN_SUMP_PX = 3.0


class ProfileLayerSetup:
    """
    Manages data source configuration for the elevation profile canvas.

    Encapsulates temporary memory layer creation (reach Z injection, node
    filtering) and elevation property / symbol configuration for all profile
    layers. Instances are owned by TwwElevationProfileWidget.
    """

    def __init__(self, canvas):
        """
        :param canvas: TwwElevationProfileCanvas instance to configure.
        """
        self._canvas = canvas
        self._temp_reach_layer = None  # Memory layer with Z values for vw_tww_reach
        self._temp_node_layer = None  # Node points: XY from ws geometry, Z = wn_bottom_level
        self._temp_cover_layer = None  # Cover points: XY from ws geometry, Z = co_level
        self._structure_cache = []  # Cached ws features for manhole dash building
        self._reach_source = None  # Original vw_tww_reach layer (for re-filtering)
        self._ws_source = None  # Original vw_tww_wastewater_structure layer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, tolerance=10.0):
        """
        Set up data sources for the elevation profile canvas.

        Based on the working configuration tested by the project owner:
        - Use "Capture curve from features" approach
        - Selected layers: vw_tww_reach, node/cover point layers, vw_change_points
        - Set all layers to "Absolute" clamping (use Z values from geometry)

        Node and cover points are memory layers built from
        vw_tww_wastewater_structure alone: its geometry is the main
        wastewater node position (sits exactly on the profile curve), Z
        values come from wn_bottom_level / co_level. No fallback —
        structures without a level are simply not rendered, keeping missing
        data visible. Neither vw_cover nor vw_wastewater_node is read.

        :param tolerance: Snapping tolerance in map units for the canvas.
        """
        # 1. CRITICAL: Set project first - this is required by QgsElevationProfileCanvas
        project = QgsProject.instance()
        self._canvas.setProject(project)

        # Build node/cover point layers from the structure view (single pass).
        self._ws_source = TwwLayerManager.layer("vw_tww_wastewater_structure")
        self._temp_node_layer, self._temp_cover_layer = self._buildStructurePointLayers(
            self._ws_source
        )

        # 2. Define the layers to use (based on working configuration)
        # profile_type: 'surface' = Continuous Surface, 'features' = Individual Features
        # Color scheme designed for professional engineering drawings
        layer_configs = [
            (
                "vw_tww_reach",
                "Reach/Pipe segments",
                "features",
                {
                    # Simple hairline (NOT the old hollow/outlined tube, whose
                    # dark outlines made the band's bottom edge look heavy). Kept
                    # only for identify/hover; the visible pipe is the exaggerated
                    # band drawn by ManholeDashPlotItem on top, whose 1.2px bottom
                    # edge covers this line so both band edges read the same.
                    "line": "#1A5276",
                    "line_width": 0.3,
                    "fill": "#1A527620",
                    "marker": "#5DADE2",
                    "marker_size": 4,
                    "marker_outline": "#1A5276",
                    "marker_name": "circle",
                    "hollow": False,
                },
            ),
            # vw_wastewater_node and vw_cover are intentionally NOT rendered: at
            # network-overview zoom their markers collapse onto the pipe invert
            # (co_level, wn_bottom_level and rp level are all within sub-pixel),
            # which contradicts the exaggerated shaft. The manhole is now drawn
            # entirely by the overlay shaft — floor edge = wn_bottom_level, cover
            # line = co_level — anchored at the true pipe invert (rp level).
            (
                "vw_change_points",
                "Change points",
                "features",
                {
                    "line": "#E74C3C",
                    "line_width": 1.5,
                    "fill": "#E74C3C20",
                    "marker": "#E74C3C",
                    "marker_size": 8,
                    "marker_outline": "#C0392B",
                    "marker_name": "diamond",
                },
            ),
        ]

        layers_to_add = []
        first_valid_crs = None

        for layer_name, _description, profile_type, style in layer_configs:
            # Node and cover entries are logical names: both are served by the
            # memory layers built above from vw_tww_wastewater_structure.
            if layer_name == "vw_wastewater_node":
                layer = self._temp_node_layer
            elif layer_name == "vw_cover":
                layer = self._temp_cover_layer
            else:
                layer = TwwLayerManager.layer(layer_name)
            if not layer:
                continue

            # Special handling for vw_tww_reach: create temp layer with Z values
            # because the original geometry doesn't have proper Z values
            if layer_name == "vw_tww_reach":
                self._reach_source = layer
                self._temp_reach_layer = self._createReachLayerWithZ(layer)
                if self._temp_reach_layer:
                    layer = self._temp_reach_layer

            # Store first valid CRS for canvas
            if first_valid_crs is None and layer.crs().isValid():
                first_valid_crs = layer.crs()

            # 3. Configure elevation properties for each layer
            elevation_props = layer.elevationProperties()
            if elevation_props and isinstance(elevation_props, QgsVectorLayerElevationProperties):
                try:
                    if hasattr(elevation_props, "setEnabled"):
                        elevation_props.setEnabled(True)

                    if hasattr(Qgis, "VectorProfileType"):
                        if profile_type == "surface":
                            elevation_props.setType(Qgis.VectorProfileType.ContinuousSurface)
                        else:
                            elevation_props.setType(Qgis.VectorProfileType.IndividualFeatures)

                    if hasattr(Qgis, "AltitudeClamping"):
                        elevation_props.setClamping(Qgis.AltitudeClamping.Absolute)
                    elif hasattr(elevation_props, "setClamping"):
                        elevation_props.setClamping(0)

                    if hasattr(Qgis, "AltitudeBinding"):
                        elevation_props.setBinding(Qgis.AltitudeBinding.Vertex)

                    # QGIS >= 3.38: Line/Polygon memory layers default to customTolerance=0
                    # (see QgsVectorLayerElevationProperties::setDefaultsFromLayer), which
                    # overrides the canvas tolerance and breaks profile/line intersection —
                    # spurious vertical segments appear at reach vertices. Use canvas tolerance.
                    if hasattr(elevation_props, "setCustomToleranceEnabled"):
                        elevation_props.setCustomToleranceEnabled(False)

                    self._configureLayerSymbols(elevation_props, style, layer_name)
                    layer.triggerRepaint()

                except Exception:
                    pass

            layers_to_add.append(layer)

        if not layers_to_add:
            print("✗ No layers found! Available layers in project:")
            for _layer_id, layer in project.mapLayers().items():
                print(f"    - {layer.name()}")
            return

        if first_valid_crs:
            self._canvas.setCrs(first_valid_crs)
        self._canvas.setLayers(layers_to_add)
        self.applyCanvasTolerance(tolerance)

    def applyCanvasTolerance(self, tolerance=10.0):
        """
        Apply canvas tolerance to all profile layers without calling setLayers().

        Safe to call on every profile refresh (QGIS >= 3.38 compatibility).
        """
        if hasattr(self._canvas, "setTolerance"):
            self._canvas.setTolerance(tolerance)

        if not hasattr(self._canvas, "layers"):
            return
        layers = self._canvas.layers()
        if not layers:
            return

        for layer in layers:
            elevation_props = layer.elevationProperties()
            if not elevation_props or not isinstance(
                elevation_props, QgsVectorLayerElevationProperties
            ):
                continue
            try:
                if hasattr(elevation_props, "setCustomToleranceEnabled"):
                    elevation_props.setCustomToleranceEnabled(False)
            except Exception:
                pass

    def updatePathFeatures(self, reach_ids=None, node_points=None):
        """
        Re-filter the temp layer contents to the currently selected path.

        Reuses the existing layer objects so the canvas is NOT re-wired with
        setLayers() (that crashes an active QgsElevationProfileCanvas). Only the
        features are swapped, which the canvas picks up on the next refresh.

        Without this, QGIS draws every reach/structure within tolerance of the
        profile curve — including side branches that merely share a node with
        the path. When reach_ids / node_points are None the layers keep every
        feature (used by the tree tool and as a safe fallback).

        :param reach_ids: iterable of selected reach obj_ids, or None.
        :param node_points: iterable of QgsPointXY of selected path nodes, or None.
        """
        if self._temp_reach_layer is not None and self._reach_source is not None:
            self._refillLayer(
                self._temp_reach_layer,
                self._reachZFeatures(self._reach_source, reach_ids),
            )

        if (
            self._ws_source is not None
            and self._temp_node_layer is not None
            and self._temp_cover_layer is not None
        ):
            node_features, cover_features, structure_cache = self._structureFeatureSets(
                self._ws_source, node_points
            )
            self._refillLayer(self._temp_node_layer, node_features)
            self._refillLayer(self._temp_cover_layer, cover_features)
            self._structure_cache = structure_cache

    @staticmethod
    def _refillLayer(layer, features):
        """Replace all features of a memory layer in place."""
        provider = layer.dataProvider()
        try:
            provider.truncate()
        except Exception:
            existing = [f.id() for f in layer.getFeatures()]
            if existing:
                provider.deleteFeatures(existing)
        if features:
            provider.addFeatures(features)
        layer.updateExtents()
        layer.triggerRepaint()

    def buildManholeDashes(self, profile_curve_geom, tolerance, default_px_width=None):
        """
        Build manhole shaft overlay data along a profile curve.

        Uses structure entries cached during setup() — no second layer scan.

        :param profile_curve_geom: QgsGeometry of the profile path.
        :param tolerance: Max distance from curve in map units.
        :param default_px_width: Fallback shaft width in pixels.
        :return: List of dash dicts for ManholeDashPlotItem.
        """
        if default_px_width is None:
            default_px_width = MANHOLE_DEFAULT_PX_WIDTH
        if profile_curve_geom is None or profile_curve_geom.isEmpty() or not self._structure_cache:
            return []

        dashes = []
        for entry in self._structure_cache:
            geometry = entry["geometry"]
            try:
                distance_along = profile_curve_geom.lineLocatePoint(geometry)
            except Exception:
                continue

            if distance_along is None or distance_along < 0:
                continue

            try:
                if profile_curve_geom.distance(geometry) > tolerance:
                    continue
            except Exception:
                pass

            dim1_mm = entry["dim1_mm"]
            cover_missing = entry["cover_level_missing"]
            bottom_missing = entry["bottom_level_missing"]
            # Pipe-invert level at this structure (rp_from/to_level), taken as the
            # nearest reach vertex Z. This is the TRUE-elevation anchor the shaft
            # is built around: cover sits above it, floor (wn_bottom_level) below
            # it (the sump). None when no Z reach is near → fall back to bottom.
            invert_level = self._adjacentReachLevel(geometry)
            dashes.append(
                {
                    "distance": float(distance_along),
                    "obj_id": entry["obj_id"],
                    "ws_type": entry.get("ws_type"),
                    "cover_level": entry["cover_level"],
                    "bottom_level": entry["bottom_level"],
                    "cover_level_missing": cover_missing,
                    "bottom_level_missing": bottom_missing,
                    "invert_level": invert_level,
                    "dim1_mm": entry["dim1_mm"],
                    "_cover_label": entry.get("_cover_label"),
                    "_bottom_label": entry.get("_bottom_label"),
                    "_input_label": entry.get("_input_label"),
                    "_output_label": entry.get("_output_label"),
                    "co_obj_id": entry.get("co_obj_id"),
                    "co_material": entry.get("co_material"),
                    "co_shape": entry.get("co_shape"),
                    "co_brand": entry.get("co_brand"),
                    "width": manhole_dash_width(dim1_mm, default_px_width),
                }
            )

        return dashes

    def _adjacentReachLevel(self, point_geom):
        """
        Invert level to anchor a manhole that has no cover and no bottom level.

        Both levels are missing, so the shaft has no true Z of its own. Rather
        than fabricate one, anchor it to the connected reach invert — the Z of
        the nearest vertex on the temp reach layer (reach endpoints sit on the
        manhole node). Returns None when no Z-bearing reach is nearby, in which
        case the canvas simply skips drawing instead of guessing a position.
        """
        if self._temp_reach_layer is None or point_geom is None:
            return None
        try:
            point = point_geom.asPoint()
        except Exception:
            return None

        best_z = None
        best_sqr = None
        for feat in self._temp_reach_layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            try:
                sqr_dist, vertex_index = geom.closestVertexWithContext(point)
            except Exception:
                continue
            if vertex_index < 0:
                continue
            if best_sqr is not None and sqr_dist >= best_sqr:
                continue
            vertex = geom.vertexAt(vertex_index)
            z_value = vertex.z()
            if z_value is None or math.isnan(z_value):
                continue
            best_sqr = sqr_dist
            best_z = z_value

        return best_z

    def buildReachBands(self, profile_curve_geom, tolerance):
        """
        Build pipe-band overlay data along the profile curve.

        QGIS renders each reach as a single invert line; to show the pipe height
        the canvas needs the invert *and* the clear height so it can draw the
        soffit above. The invert is anchored to the true level (Z); the soffit
        is offset upward by an exaggerated pixel thickness (see reach_band_px),
        because the real clear height is sub-pixel at network-overview zoom.
        This returns, per reach, the invert vertices as (distance, invert_Z)
        plus raw clear_height in mm (None when missing — only the invert line is
        then drawn, never a fabricated height).

        Reuses the temp reach layer (LineStringZ, Z = interpolated invert).
        """
        if (
            self._temp_reach_layer is None
            or profile_curve_geom is None
            or profile_curve_geom.isEmpty()
        ):
            return []

        bands = []
        for feat in self._temp_reach_layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            try:
                if profile_curve_geom.distance(geom) > tolerance:
                    continue
            except Exception:
                pass

            points = []
            for vertex in geom.vertices():
                z_value = vertex.z()
                if z_value is None or math.isnan(z_value):
                    continue
                try:
                    distance_along = profile_curve_geom.lineLocatePoint(
                        QgsGeometry(QgsPoint(vertex.x(), vertex.y()))
                    )
                except Exception:
                    continue
                if distance_along is None or distance_along < 0:
                    continue
                points.append((float(distance_along), float(z_value)))

            if len(points) < 2:
                continue
            points.sort(key=lambda item: item[0])

            attrs = _feature_attributes(feat)
            bands.append(
                {
                    "obj_id": attrs.get("obj_id"),
                    "invert": points,
                    "clear_height_mm": _to_float(attrs.get("clear_height")),
                }
            )

        return bands

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _createReachLayerWithZ(self, original_layer, reach_ids=None):
        """
        Create a temporary memory layer from vw_tww_reach with proper Z values.

        Since vw_tww_reach geometry doesn't have Z values, we read rp_from_level
        and rp_to_level attributes and set them as the Z coordinates.

        :param original_layer: The original vw_tww_reach layer
        :param reach_ids: optional iterable of reach obj_ids to keep (path filter)
        :return: Memory layer with LineStringZ geometry containing proper Z values
        """
        if original_layer is None:
            return None

        crs = original_layer.crs()
        crs_string = crs.authid() if crs.isValid() else "EPSG:2056"

        mem_layer = QgsVectorLayer(
            f"LineStringZ?crs={crs_string}",
            "reach_with_z",
            "memory",
        )
        provider = mem_layer.dataProvider()
        provider.addAttributes(original_layer.fields().toList())
        mem_layer.updateFields()

        provider.addFeatures(self._reachZFeatures(original_layer, reach_ids))
        mem_layer.updateExtents()
        return mem_layer

    def _reachZFeatures(self, original_layer, reach_ids=None):
        """
        Build LineStringZ features from vw_tww_reach, interpolating Z from
        rp_from_level / rp_to_level. Reaches missing either level are dropped.

        :param reach_ids: when not None, only reaches whose obj_id is in this
            set are included — this restricts the profile to the selected path
            instead of every reach that happens to lie near the curve.
        """
        id_filter = set(reach_ids) if reach_ids is not None else None
        features = []

        for feat in original_layer.getFeatures():
            if id_filter is not None:
                try:
                    if feat["obj_id"] not in id_filter:
                        continue
                except KeyError:
                    continue

            from_level = None
            to_level = None

            try:
                from_level = feat["rp_from_level"]
                to_level = feat["rp_to_level"]
            except KeyError:
                pass

            if from_level is None or to_level is None:
                continue

            try:
                from_level = float(from_level)
                to_level = float(to_level)
            except (TypeError, ValueError):
                continue

            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue

            vertices = list(geom.vertices())
            if len(vertices) < 2:
                continue

            new_points = []
            total_length = geom.length()

            if total_length > 0 and len(vertices) > 2:
                accumulated_length = 0.0
                for i, vertex in enumerate(vertices):
                    if i == 0:
                        z_value = from_level
                    elif i == len(vertices) - 1:
                        z_value = to_level
                    else:
                        prev_vertex = vertices[i - 1]
                        segment_length = QgsPointXY(prev_vertex.x(), prev_vertex.y()).distance(
                            QgsPointXY(vertex.x(), vertex.y())
                        )
                        accumulated_length += segment_length
                        ratio = accumulated_length / total_length
                        z_value = from_level + (to_level - from_level) * ratio

                    new_points.append(QgsPoint(vertex.x(), vertex.y(), z_value))
            else:
                new_points.append(QgsPoint(vertices[0].x(), vertices[0].y(), from_level))
                new_points.append(QgsPoint(vertices[-1].x(), vertices[-1].y(), to_level))

            new_line = QgsLineString(new_points)
            new_geom = QgsGeometry(new_line)
            new_feat = QgsFeature()
            new_feat.setGeometry(new_geom)
            new_feat.setAttributes(feat.attributes())
            features.append(new_feat)

        return features

    def _buildStructurePointLayers(self, ws_layer, node_points=None):
        """
        Build node and cover PointZ memory layers in one pass over
        vw_tww_wastewater_structure, caching entries for manhole dashes.

        :param node_points: optional iterable of QgsPointXY of selected path
            nodes; when given only structures sitting on a path node are kept.
        """
        if ws_layer is None:
            self._structure_cache = []
            return None, None

        crs = ws_layer.crs()
        crs_string = crs.authid() if crs.isValid() else "EPSG:2056"

        node_layer = QgsVectorLayer(
            f"PointZ?crs={crs_string}", "wastewater_node_filtered", "memory"
        )
        cover_layer = QgsVectorLayer(f"PointZ?crs={crs_string}", "cover_from_structure", "memory")

        for mem_layer in (node_layer, cover_layer):
            provider = mem_layer.dataProvider()
            provider.addAttributes(ws_layer.fields().toList())
            mem_layer.updateFields()

        node_features, cover_features, structure_cache = self._structureFeatureSets(
            ws_layer, node_points
        )

        node_layer.dataProvider().addFeatures(node_features)
        cover_layer.dataProvider().addFeatures(cover_features)
        node_layer.updateExtents()
        cover_layer.updateExtents()
        self._structure_cache = structure_cache
        return node_layer, cover_layer

    def _structureFeatureSets(self, ws_layer, node_points=None):
        """
        Scan vw_tww_wastewater_structure once and return
        (node_features, cover_features, structure_cache).

        When node_points is not None, a structure is only included if its
        profile point coincides with one of the selected path's node points —
        this keeps side-branch structures out of the profile.
        """
        path_points = list(node_points) if node_points is not None else None

        node_features = []
        cover_features = []
        structure_cache = []

        for feat in ws_layer.getFeatures():
            attrs = _feature_attributes(feat)
            point_geom = _structure_profile_point(feat)
            if point_geom is None:
                continue

            try:
                point = point_geom.asPoint()
            except Exception:
                continue

            if path_points is not None and not _point_on_path(point, path_points):
                continue

            cover_level, bottom_level, cover_missing, bottom_missing = _manhole_level_state(
                _to_float(attrs.get("co_level")),
                _to_float(attrs.get("wn_bottom_level")),
            )

            if bottom_level is not None:
                node_feat = QgsFeature()
                node_feat.setGeometry(
                    QgsGeometry(QgsPoint(point.x(), point.y(), bottom_level))
                )
                node_feat.setAttributes(feat.attributes())
                node_features.append(node_feat)

            if cover_level is not None:
                cover_feat = QgsFeature()
                cover_feat.setGeometry(
                    QgsGeometry(QgsPoint(point.x(), point.y(), cover_level))
                )
                cover_feat.setAttributes(feat.attributes())
                cover_features.append(cover_feat)

            # Both-missing structures used to be dropped here. They are now kept
            # so the canvas can draw an explicit "no level data" dashed shaft;
            # no node/cover feature is added (both guards above failed), so
            # nothing is fabricated for the rendered reach/point layers.
            structure_cache.append(
                {
                    "geometry": point_geom,
                    "obj_id": attrs.get("obj_id"),
                    "ws_type": attrs.get("ws_type"),
                    "cover_level": cover_level,
                    "bottom_level": bottom_level,
                    "cover_level_missing": cover_missing,
                    "bottom_level_missing": bottom_missing,
                    "dim1_mm": _to_float(attrs.get("ma_dimension1")),
                    "_cover_label": attrs.get("_cover_label"),
                    "_bottom_label": attrs.get("_bottom_label"),
                    "_input_label": attrs.get("_input_label"),
                    "_output_label": attrs.get("_output_label"),
                    # Cover attributes for the dedicated cover-cap tooltip.
                    "co_obj_id": attrs.get("co_obj_id"),
                    "co_material": attrs.get("co_material"),
                    "co_shape": attrs.get("co_shape"),
                    "co_brand": attrs.get("co_brand"),
                }
            )

        return node_features, cover_features, structure_cache

    def _configureLayerSymbols(self, elevation_props, style, layer_name):
        """
        Configure profile symbols for a layer to improve visual appearance.

        :param elevation_props: QgsVectorLayerElevationProperties
        :param style: dict with 'line', 'line_width', 'fill', 'marker', 'marker_size'.
                      Optional 'hollow': True to create hollow/outline effect for lines.
        :param layer_name: Name of the layer (for logging)
        """
        try:
            if hasattr(elevation_props, "setProfileLineSymbol"):
                if style.get("hollow", False):
                    line_symbol = QgsLineSymbol()
                    line_symbol.deleteSymbolLayer(0)

                    inner_width = style["line_width"]
                    inner_layer = QgsSimpleLineSymbolLayer()
                    inner_layer.setColor(QColor(style.get("line_inner", "#FFFFFF")))
                    inner_layer.setWidth(inner_width)
                    inner_layer.setPenCapStyle(Qt.PenCapStyle.RoundCap)
                    inner_layer.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    line_symbol.appendSymbolLayer(inner_layer)

                    outline_width = style.get("outline_width", 0.8)
                    outline_layer = QgsSimpleLineSymbolLayer()
                    outline_layer.setColor(QColor(style["line"]))
                    outline_layer.setWidth(outline_width)
                    outline_layer.setOffset(inner_width / 2 - outline_width / 2)
                    outline_layer.setPenCapStyle(Qt.PenCapStyle.RoundCap)
                    outline_layer.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    line_symbol.appendSymbolLayer(outline_layer)

                    bottom_layer = QgsSimpleLineSymbolLayer()
                    bottom_layer.setColor(QColor(style["line"]))
                    bottom_layer.setWidth(outline_width)
                    bottom_layer.setOffset(-(inner_width / 2 - outline_width / 2))
                    bottom_layer.setPenCapStyle(Qt.PenCapStyle.RoundCap)
                    bottom_layer.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    line_symbol.appendSymbolLayer(bottom_layer)
                else:
                    line_symbol = QgsLineSymbol.createSimple(
                        {
                            "color": style["line"],
                            "width": str(style["line_width"]),
                            "capstyle": "round",
                            "joinstyle": "round",
                        }
                    )
                elevation_props.setProfileLineSymbol(line_symbol)

            if hasattr(elevation_props, "setProfileFillSymbol"):
                fill_symbol = QgsFillSymbol.createSimple(
                    {
                        "color": style["fill"],
                        "outline_color": style["line"],
                        "outline_width": "0.5",
                    }
                )
                elevation_props.setProfileFillSymbol(fill_symbol)

            if hasattr(elevation_props, "setProfileMarkerSymbol"):
                marker_symbol = QgsMarkerSymbol.createSimple(
                    {
                        "color": style["marker"],
                        "size": str(style["marker_size"]),
                        "outline_color": style.get("marker_outline", "#FFFFFF"),
                        "outline_width": "0.5",
                        "name": style.get("marker_name", "circle"),
                    }
                )
                elevation_props.setProfileMarkerSymbol(marker_symbol)
                if hasattr(elevation_props, "setShowMarkers"):
                    elevation_props.setShowMarkers(True)
                if hasattr(elevation_props, "setShowMarker"):
                    elevation_props.setShowMarker(True)
                if hasattr(elevation_props, "setShowPoints"):
                    elevation_props.setShowPoints(True)

            if hasattr(elevation_props, "setRespectLayerSymbology"):
                elevation_props.setRespectLayerSymbology(False)
        except Exception:
            pass


# ------------------------------------------------------------------
# Module-level utility functions (shared with hover_manager via import)
# ------------------------------------------------------------------


def _feature_attributes(feature):
    """Extract {name: value} dict from a QgsFeature."""
    if feature is None:
        return {}
    try:
        fields = feature.fields()
        names = fields.names() if hasattr(fields, "names") else []
        attrs = {}
        for name in names:
            try:
                attrs[name] = feature.attribute(name)
            except Exception:
                continue
        return attrs
    except Exception:
        return {}


def _pick_attr(attrs, keys):
    """Look up a value by trying multiple candidate keys (case-insensitive fallback)."""
    if not attrs:
        return None
    for key in keys:
        if key in attrs:
            return attrs.get(key)
    lower_map = {str(k).lower(): v for k, v in attrs.items()}
    for key in keys:
        if key.lower() in lower_map:
            return lower_map[key.lower()]
    return None


def _point_on_path(point, path_points, max_sqr_dist=0.01):
    """
    True if ``point`` coincides with one of the selected path's node points.

    A structure's profile point is the main wastewater-node position, which is
    exactly the same coordinate as the corresponding path node, so an exact
    match (within a 0.1 m tolerance) reliably keeps path structures and rejects
    branch structures sitting elsewhere.
    """
    for path_point in path_points:
        try:
            if point.sqrDist(path_point) <= max_sqr_dist:
                return True
        except Exception:
            continue
    return False


def _to_float(value):
    """Safely convert a value to float, returning None on failure."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _structure_profile_point(feat):
    """Return a point QgsGeometry for profile placement, or None."""
    geom = feat.geometry()
    if geom is None or geom.isEmpty():
        return None
    try:
        if geom.type() != Qgis.GeometryType.Point:
            geom = geom.centroid()
        if geom is None or geom.isEmpty():
            return None
        return geom
    except Exception:
        return None


def _manhole_level_state(cover_level, bottom_level):
    """
    Parse cover/bottom levels and missing flags.

    :return: (cover_level, bottom_level, cover_missing, bottom_missing)
    """
    cover_missing = cover_level is None
    bottom_missing = bottom_level is None or bottom_level == 0
    if bottom_missing:
        bottom_level = None
    return cover_level, bottom_level, cover_missing, bottom_missing


def _resolve_manhole_anchors(cover_level, bottom_level):
    """When one level is missing, anchor drawing/hit-test at the known level."""
    anchor_cover = cover_level if cover_level is not None else bottom_level
    anchor_bottom = bottom_level if bottom_level is not None else cover_level
    return anchor_cover, anchor_bottom


def manhole_dash_width(dim1_mm, default_px=MANHOLE_DEFAULT_PX_WIDTH):
    """
    Shaft width in pixels from ma_dimension1 (mm).

    A manhole is ~0.6-0.9 m wide, which on the distance axis (hundreds of
    metres) would be sub-pixel — so width is a *schematic, exaggerated* glyph,
    not to scale. ``dim1_mm / 25`` spreads the narrow real range across a
    readable 14-48 px band so bigger manholes look bigger; the true dimension
    stays in the tooltip. Missing dimension falls back to the narrowest width.
    """
    if dim1_mm is None:
        return default_px
    return max(14.0, min(48.0, float(dim1_mm) / 25.0))


def reach_band_px(clear_height_mm):
    """
    Schematic pipe-band thickness in px from clear_height (mm).

    Uses the SAME vertical exaggeration as the manhole (MANHOLE_VERTICAL_GAIN_PX),
    so a pipe is always drawn smaller than the manhole it connects to — a pipe's
    clear height is physically less than the manhole's depth, so a band taller
    than its manhole (the old clear_height/40 ≈ 25 px/m, ~4× the manhole scale)
    read wrong. A small minimum keeps thin pipes visible. Missing clear_height
    returns None → only the invert line is drawn.
    """
    if clear_height_mm is None:
        return None
    px = (float(clear_height_mm) / 1000.0) * MANHOLE_VERTICAL_GAIN_PX
    return max(4.0, min(MANHOLE_MAX_SHAFT_PX, px))


def manhole_shaft_px(depth_m):
    """
    Schematic manhole shaft height in px from the real depth (cover-bottom, m).

    Fallback used only when the pipe-invert level is unknown; otherwise the
    shaft is split into cover and sump offsets around the invert (see below).
    Exaggerated and proportional so a deeper manhole looks taller at any zoom.
    """
    if depth_m is None or depth_m <= 0:
        return MANHOLE_MIN_SHAFT_PX
    return max(
        MANHOLE_MIN_SHAFT_PX,
        min(MANHOLE_MAX_SHAFT_PX, float(depth_m) * MANHOLE_VERTICAL_GAIN_PX),
    )


def manhole_cover_offset_px(cover_above_invert_m):
    """Exaggerated px from the pipe invert UP to the cover (co_level - rp)."""
    if cover_above_invert_m is None or cover_above_invert_m <= 0:
        return MANHOLE_MIN_COVER_PX
    return max(
        MANHOLE_MIN_COVER_PX,
        min(MANHOLE_MAX_SHAFT_PX, float(cover_above_invert_m) * MANHOLE_VERTICAL_GAIN_PX),
    )


def manhole_sump_offset_px(invert_above_floor_m):
    """Exaggerated px from the pipe invert DOWN to the floor (rp - wn_bottom_level)."""
    if invert_above_floor_m is None or invert_above_floor_m <= 0:
        return MANHOLE_MIN_SUMP_PX
    return max(
        MANHOLE_MIN_SUMP_PX,
        min(MANHOLE_MAX_SHAFT_PX, float(invert_above_floor_m) * MANHOLE_VERTICAL_GAIN_PX),
    )


# ------------------------------------------------------------------
# Value-list (code -> localized term) resolution
# ------------------------------------------------------------------
#
# The TEKSI views expose value-list fields (material, cover shape, ...) as raw
# integer codes — e.g. vw_tww_reach.material = 3639. The tooltips must show the
# human-readable term ("concrete_insitu"), so codes are resolved against the
# tww_vl.<table> dictionaries. Each table is tiny and loaded once, then cached
# for the session; resolution failures fall back to the raw code (never worse
# than before) so a missing DB connection cannot break the tooltip.

_VALUE_LIST_LANGS = ("en", "de", "fr", "it", "ro")
_value_list_cache = {}  # table name -> {code: term}


def _value_list_language():
    """Return the 2-letter language the TEKSI value lists should be shown in."""
    name = ""
    try:
        from qgis.PyQt.QtCore import QLocale, QSettings

        settings = QSettings()
        if settings.value("locale/overrideFlag", False, type=bool):
            name = settings.value("locale/userLocale", "") or ""
        else:
            name = QLocale.system().name()
    except Exception:
        name = ""
    lang = str(name)[:2].lower()
    return lang if lang in _VALUE_LIST_LANGS else "en"


def _load_value_list(table):
    """Load and cache a ``tww_vl.<table>`` code -> localized term mapping."""
    if table in _value_list_cache:
        return _value_list_cache[table]

    lang = _value_list_language()
    try:
        # value_<lang> with English/German fallbacks for incomplete dictionaries.
        # ``table`` is always a hard-coded constant from the callers below.
        rows = DatabaseUtils.fetchall(
            f"SELECT code, COALESCE(value_{lang}, value_en, value_de) "
            f"FROM tww_vl.{table}"
        )
    except Exception:
        # DB not reachable yet — return without caching so a later hover retries.
        return {}

    mapping = {}
    for code, term in rows or []:
        if code is None or term is None:
            continue
        try:
            mapping[int(code)] = str(term)
        except (TypeError, ValueError):
            continue

    _value_list_cache[table] = mapping
    return mapping


def resolve_value_list(table, code):
    """
    Resolve an integer value-list code to its localized term.

    :param table: the ``tww_vl`` dictionary table name (e.g. ``"reach_material"``).
    :param code: the raw code from the view (int, numeric string, or None).
    :return: the term, or None when the code is empty or cannot be resolved
        (DB unavailable / code absent) — callers then fall back to the raw code.
    """
    if code is None or code == "":
        return None
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        return None
    return _load_value_list(table).get(code_int)
