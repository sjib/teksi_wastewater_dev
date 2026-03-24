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
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsSimpleLineSymbolLayer,
    QgsVectorLayer,
    QgsVectorLayerElevationProperties,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ...utils.twwlayermanager import TwwLayerManager


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
        self._temp_node_layer = None  # Filtered memory layer for vw_wastewater_node

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, tolerance=10.0):
        """
        Set up data sources for the elevation profile canvas.

        Based on the working configuration tested by the project owner:
        - Use "Capture curve from features" approach
        - Selected layers: vw_tww_reach, vw_wastewater_node, vw_cover, vw_change_points
        - Set all layers to "Absolute" clamping (use Z values from geometry)

        :param tolerance: Snapping tolerance in map units for the canvas.
        """
        # 1. CRITICAL: Set project first - this is required by QgsElevationProfileCanvas
        project = QgsProject.instance()
        self._canvas.setProject(project)

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

            # Special handling for vw_wastewater_node: filter out nodes with
            # bottom_level = 0 or NULL so they don't appear at elevation 0
            if layer_name == "vw_wastewater_node":
                self._temp_node_layer = self._createFilteredNodeLayer(layer)
                if self._temp_node_layer:
                    layer = self._temp_node_layer

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
        self._canvas.setTolerance(tolerance)

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
        skipped_no_level = 0
        skipped_no_vertices = 0

        for feat in original_layer.getFeatures():
            from_level = None
            to_level = None

            try:
                from_level = feat["rp_from_level"]
                to_level = feat["rp_to_level"]
            except KeyError:
                pass

            if from_level is None or to_level is None:
                skipped_no_level += 1
                continue

            try:
                from_level = float(from_level)
                to_level = float(to_level)
            except (TypeError, ValueError):
                skipped_no_level += 1
                continue

            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue

            vertices = list(geom.vertices())
            if len(vertices) < 2:
                skipped_no_vertices += 1
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
            from qgis.core import QgsGeometry

            new_geom = QgsGeometry(new_line)
            new_feat = QgsFeature()
            new_feat.setGeometry(new_geom)
            new_feat.setAttributes(feat.attributes())
            features.append(new_feat)

        provider.addFeatures(features)
        mem_layer.updateExtents()
        return mem_layer

    def _createFilteredNodeLayer(self, original_layer):
        """
        Create a filtered copy of vw_wastewater_node that excludes nodes
        with bottom_level = 0 or NULL, so they don't appear at elevation 0
        in the profile canvas.

        :param original_layer: The original vw_wastewater_node layer
        :return: Memory layer with only valid nodes, or None if creation fails
        """
        if original_layer is None:
            return None

        crs = original_layer.crs()
        crs_string = crs.authid() if crs.isValid() else "EPSG:2056"

        geom_str = "Point"
        if original_layer.wkbType() in (
            QgsWkbTypes.PointZ,
            QgsWkbTypes.MultiPointZ,
            QgsWkbTypes.Point25D,
        ):
            geom_str = "PointZ"

        mem_layer = QgsVectorLayer(
            f"{geom_str}?crs={crs_string}",
            "wastewater_node_filtered",
            "memory",
        )
        provider = mem_layer.dataProvider()
        provider.addAttributes(original_layer.fields().toList())
        mem_layer.updateFields()

        features = []
        for feat in original_layer.getFeatures():
            attrs = _feature_attributes(feat)
            bottom_level = _to_float(_pick_attr(attrs, ["bottom_level", "bottomLevel", "invert_level"]))
            # Skip nodes with missing or zero bottom_level
            if bottom_level is None or bottom_level == 0:
                continue

            new_feat = QgsFeature()
            new_feat.setGeometry(feat.geometry())
            new_feat.setAttributes(feat.attributes())
            features.append(new_feat)

        provider.addFeatures(features)
        mem_layer.updateExtents()
        return mem_layer

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
                    inner_layer.setPenCapStyle(Qt.FlatCap)
                    inner_layer.setPenJoinStyle(Qt.RoundJoin)
                    line_symbol.appendSymbolLayer(inner_layer)

                    outline_width = style.get("outline_width", 0.8)
                    outline_layer = QgsSimpleLineSymbolLayer()
                    outline_layer.setColor(QColor(style["line"]))
                    outline_layer.setWidth(outline_width)
                    outline_layer.setOffset(inner_width / 2 - outline_width / 2)
                    outline_layer.setPenCapStyle(Qt.FlatCap)
                    outline_layer.setPenJoinStyle(Qt.RoundJoin)
                    line_symbol.appendSymbolLayer(outline_layer)

                    bottom_layer = QgsSimpleLineSymbolLayer()
                    bottom_layer.setColor(QColor(style["line"]))
                    bottom_layer.setWidth(outline_width)
                    bottom_layer.setOffset(-(inner_width / 2 - outline_width / 2))
                    bottom_layer.setPenCapStyle(Qt.FlatCap)
                    bottom_layer.setPenJoinStyle(Qt.RoundJoin)
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
