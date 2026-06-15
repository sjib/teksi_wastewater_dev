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

from ...utils.twwlayermanager import TwwLayerManager

MANHOLE_DEFAULT_PX_WIDTH = 10


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
        ws_source = TwwLayerManager.layer("vw_tww_wastewater_structure")
        self._temp_node_layer, self._temp_cover_layer = self._buildStructurePointLayers(ws_source)

        # 2. Define the layers to use (based on working configuration)
        # profile_type: 'surface' = Continuous Surface, 'features' = Individual Features
        # Color scheme designed for professional engineering drawings
        layer_configs = [
            (
                "vw_tww_reach",
                "Reach/Pipe segments",
                "features",
                {
                    "line": "#1A5276",
                    "line_width": 4.0,
                    "fill": "#1A527620",
                    "marker": "#5DADE2",
                    "marker_size": 4,
                    "marker_outline": "#1A5276",
                    "marker_name": "circle",
                    "hollow": True,
                    "line_inner": "#E8F6FF",
                    "outline_width": 1.0,
                },
            ),
            (
                "vw_wastewater_node",
                "Wastewater nodes",
                "features",
                {
                    "line": "#8E44AD",
                    "line_width": 1.0,
                    "fill": "#8E44AD30",
                    "marker": "#8E44AD",
                    "marker_size": 5,
                },
            ),
            (
                "vw_cover",
                "Covers",
                "features",
                {
                    "line": "#00000000",
                    "line_width": 0.1,
                    "fill": "#00000000",
                    "marker": "#27AE60",
                    "marker_size": 6,
                    "marker_outline": "#1E8449",
                    "marker_name": "circle",
                },
            ),
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
                # Fallback for potential naming differences in DB/views.
                if not layer and layer_name == "vw_change_points":
                    layer = TwwLayerManager.layer("vm_change_points")
            if not layer:
                continue

            # Special handling for vw_tww_reach: create temp layer with Z values
            # because the original geometry doesn't have proper Z values
            if layer_name == "vw_tww_reach":
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
            dashes.append(
                {
                    "distance": float(distance_along),
                    "obj_id": entry["obj_id"],
                    "cover_level": entry["cover_level"],
                    "bottom_level": entry["bottom_level"],
                    "cover_level_missing": entry["cover_level_missing"],
                    "bottom_level_missing": entry["bottom_level_missing"],
                    "width": manhole_dash_width(dim1_mm, default_px_width),
                }
            )

        return dashes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _createReachLayerWithZ(self, original_layer):
        """
        Create a temporary memory layer from vw_tww_reach with proper Z values.

        Since vw_tww_reach geometry doesn't have Z values, we read rp_from_level
        and rp_to_level attributes and set them as the Z coordinates.

        :param original_layer: The original vw_tww_reach layer
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

        features = []

        for feat in original_layer.getFeatures():
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

        provider.addFeatures(features)
        mem_layer.updateExtents()
        return mem_layer

    def _buildStructurePointLayers(self, ws_layer):
        """
        Build node and cover PointZ memory layers in one pass over
        vw_tww_wastewater_structure, caching entries for manhole dashes.
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

            cover_level, bottom_level, cover_missing, bottom_missing = _manhole_level_state(
                _to_float(_pick_attr(attrs, ["co_level"])),
                _to_float(_pick_attr(attrs, ["wn_bottom_level"])),
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

            if not (cover_missing and bottom_missing):
                structure_cache.append(
                    {
                        "geometry": point_geom,
                        "obj_id": _pick_attr(attrs, ["wn_obj_id"]),
                        "cover_level": cover_level,
                        "bottom_level": bottom_level,
                        "cover_level_missing": cover_missing,
                        "bottom_level_missing": bottom_missing,
                        "dim1_mm": _to_float(_pick_attr(attrs, ["ma_dimension1"])),
                    }
                )

        node_layer.dataProvider().addFeatures(node_features)
        cover_layer.dataProvider().addFeatures(cover_features)
        node_layer.updateExtents()
        cover_layer.updateExtents()
        self._structure_cache = structure_cache
        return node_layer, cover_layer

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
    """Shaft line width in pixels from ma_dimension1 (mm)."""
    if dim1_mm is None:
        return default_px
    return max(6.0, min(16.0, float(dim1_mm) / 100.0))
