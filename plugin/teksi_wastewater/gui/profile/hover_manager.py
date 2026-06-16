# -----------------------------------------------------------
#
# Elevation Profile — Hover, Tooltip & Map Highlight Manager
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

from qgis.core import QgsFeatureRequest
from qgis.gui import QgsHighlight
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QLabel

from ...utils.twwlayermanager import TwwLayerManager
from .layer_setup import (
    _feature_attributes,
    _resolve_manhole_anchors,
    _to_float,
)


class ProfileHoverManager:
    """
    Manages hover interaction for the elevation profile canvas.

    Responsibilities:
    - Mouse event routing (mouseMoveEvent → hover detection)
    - Feature identification via official identify() API + custom manhole hit-test
    - Persistent custom tooltip (QLabel) with rich-text content
    - Map canvas highlight synchronised with profile hover
    """

    # Highlight colors for map canvas feedback
    HIGHLIGHT_COLOR = QColor("#2ECC71")  # Emerald green
    HIGHLIGHT_FILL_COLOR = QColor(46, 204, 113, 60)  # Semi-transparent fill

    MISSING_DATA_HTML = '<span style="color:red">Missing Data</span>'

    # Canonical vw_tww_reach attribute names (see tww_app.vw_tww_reach).
    REACH_OBJ_ID = "obj_id"
    REACH_MATERIAL = "material"
    REACH_CLEAR_HEIGHT = "clear_height"
    REACH_LENGTH_EFFECTIVE = "length_effective"
    REACH_SLOPE_PER_MILL = "_slope_per_mill"
    REACH_RP_FROM_LEVEL = "rp_from_level"
    REACH_RP_TO_LEVEL = "rp_to_level"

    # Canonical main-cover fields on vw_tww_wastewater_structure (co_* from main_co).
    COVER_OBJ_ID = "co_obj_id"
    COVER_LEVEL = "co_level"
    COVER_MATERIAL = "co_material"
    COVER_SHAPE = "co_shape"
    COVER_BRAND = "co_brand"

    # Canonical vw_tww_wastewater_structure fields for manhole hover tooltips.
    MANHOLE_OBJ_ID = "obj_id"
    MANHOLE_WS_TYPE = "ws_type"
    MANHOLE_CO_LEVEL = "co_level"
    MANHOLE_BOTTOM_LEVEL = "wn_bottom_level"
    MANHOLE_COVER_LABEL = "_cover_label"
    MANHOLE_BOTTOM_LABEL = "_bottom_label"
    MANHOLE_INPUT_LABEL = "_input_label"
    MANHOLE_OUTPUT_LABEL = "_output_label"
    MANHOLE_DIMENSION1 = "ma_dimension1"

    def __init__(self, canvas, map_canvas):
        """
        :param canvas: TwwElevationProfileCanvas instance.
        :param map_canvas: QgsMapCanvas from iface (may be None in tests).
        """
        self._canvas = canvas
        self._map_canvas = map_canvas

        # Hover state
        self._hover_enabled = True
        self._last_hover_match = None
        self._last_hover_pos = None
        self._last_hover_global_pos = None
        self._last_tooltip_text = None

        # Map highlight state
        self._current_highlight = None
        self._current_highlight_key = None

        # Persistent tooltip widget (avoids QToolTip auto-timeout)
        self._custom_tooltip = QLabel(None)
        self._custom_tooltip.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self._custom_tooltip.setStyleSheet(
            """
            QLabel {
                background-color: #ffffcc;
                border: 1px solid #000000;
                padding: 4px;
                color: #000000;
                font-family: monospace;
                font-size: 10pt;
            }
        """
        )
        self._custom_tooltip.hide()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self):
        """
        Connect canvas signals for hover handling.

        Call once after the canvas has been created.
        """
        if hasattr(self._canvas, "setSnappingEnabled"):
            self._canvas.setSnappingEnabled(True)

    # ------------------------------------------------------------------
    # Event handlers (called by TwwElevationProfileCanvas callbacks)
    # ------------------------------------------------------------------

    def onCanvasMouseMove(self, event):
        """Handle raw mouseMoveEvent forwarded from the canvas."""
        if not self._hover_enabled:
            return
        self._last_hover_pos = event.pos()
        self._last_hover_global_pos = self._canvas.mapToGlobal(event.pos())
        self._handleCanvasHover(event.pos())

    def onCanvasLeave(self, _event):
        """Handle leaveEvent forwarded from the canvas."""
        self.clearState()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clearState(self):
        """Clear all hover state: tooltip, highlight, last match."""
        self._last_hover_match = None
        self._last_tooltip_text = None
        self._custom_tooltip.hide()
        self._clearHighlight()

    # ------------------------------------------------------------------
    # Internal hover pipeline
    # ------------------------------------------------------------------

    def _handleCanvasHover(self, pos):
        """Handle hover using raw canvas pixel coordinates."""
        if not hasattr(self._canvas, "canvasPointToPlotPoint"):
            return
        if hasattr(self._canvas, "plotArea"):
            try:
                plot_area = self._canvas.plotArea()
                if plot_area and not plot_area.contains(QPointF(pos)):
                    self.clearState()
                    return
            except Exception:
                pass
        if hasattr(self._canvas, "snapToPlot"):
            try:
                self._canvas.snapToPlot(pos)
            except Exception:
                pass
        profile_point = self._canvas.canvasPointToPlotPoint(QPointF(pos))
        if self._isEmptyProfilePoint(profile_point):
            self.clearState()
            return
        self._updateHoverMatch(profile_point)

    def _updateHoverMatch(self, profile_point):
        """
        Convert hover to plot coordinates and match the nearest profile result.

        Uses official identify() API first, then custom manhole hit-test.
        """
        plot_point = self._profilePointToPlotPoint(profile_point)
        if plot_point is None:
            self.clearState()
            return

        identify_results = []
        if hasattr(self._canvas, "identify") and self._last_hover_pos is not None:
            try:
                canvas_point = QPointF(self._last_hover_pos)
                identify_results = self._canvas.identify(canvas_point)
            except Exception:
                pass

        manhole_match = self._identifyManholeDash(plot_point)
        if manhole_match:
            identify_results.append(manhole_match)

        nearest = self._nearestIdentifyResult(identify_results, plot_point)

        if nearest:
            self._last_hover_match = nearest

        self._showHoverTooltip(plot_point, self._last_hover_match)

        if self._last_hover_match:
            self._highlightMatchOnMap(plot_point, self._last_hover_match)
        else:
            self._clearHighlight()

    # ------------------------------------------------------------------
    # Manhole dash hit-test
    # ------------------------------------------------------------------

    def _identifyManholeDash(self, plot_point):
        """
        Identify custom-drawn manhole dashes (not in layers, drawn via ManholeDashPlotItem).

        :param plot_point: QPointF with (distance, elevation) in plot coordinates.
        :return: Fake identify-result dict, or None if no match.
        """
        if not hasattr(self._canvas, "getManholeDashes"):
            return None
        manhole_dashes = self._canvas.getManholeDashes()
        if not manhole_dashes:
            return None

        distance = plot_point.x()
        elevation = plot_point.y()

        tolerance_dist = 5.0
        tolerance_elev = 5.0

        best_match = None
        best_distance2 = float("inf")

        for dash in manhole_dashes:
            dash_distance = dash.get("distance")
            cover_level = dash.get("cover_level")
            bottom_level = dash.get("bottom_level")

            if dash_distance is None or (cover_level is None and bottom_level is None):
                continue

            eff_cover, eff_bottom = _resolve_manhole_anchors(cover_level, bottom_level)

            dist_diff = abs(distance - dash_distance)
            if dist_diff > tolerance_dist:
                continue

            min_elev = min(eff_bottom, eff_cover) - tolerance_elev
            max_elev = max(eff_bottom, eff_cover) + tolerance_elev

            if not (min_elev <= elevation <= max_elev):
                continue

            center_elev = (eff_cover + eff_bottom) / 2
            elev_diff = abs(elevation - center_elev)
            distance2 = dist_diff * dist_diff + elev_diff * elev_diff

            if distance2 < best_distance2:
                best_distance2 = distance2
                best_match = {
                    "layer": None,
                    "result": {
                        "feature": None,
                        "attributes": {
                            "obj_id": dash.get("obj_id"),
                            "ws_type": dash.get("ws_type", "manhole"),
                            "co_level": cover_level,
                            "wn_bottom_level": bottom_level,
                            "cover_level_missing": dash.get("cover_level_missing", False),
                            "bottom_level_missing": dash.get("bottom_level_missing", False),
                            "_cover_label": dash.get("_cover_label"),
                            "_bottom_label": dash.get("_bottom_label"),
                            "_input_label": dash.get("_input_label"),
                            "_output_label": dash.get("_output_label"),
                            "ma_dimension1": dash.get("dim1_mm"),
                            "_is_manhole_dash": True,
                        },
                        "distance": dash_distance,
                        "elevation": center_elev,
                    },
                    "plot_point": QPointF(dash_distance, center_elev),
                    "distance2": distance2,
                }

        return best_match

    # ------------------------------------------------------------------
    # Identify result helpers
    # ------------------------------------------------------------------

    def _nearestIdentifyResult(self, identify_results, plot_point):
        """Pick the nearest identify result to a plot point."""
        if not identify_results:
            return None

        best_match = None
        fallback_match = None

        for identify_result in identify_results:
            if isinstance(identify_result, dict):
                if best_match is None or identify_result["distance2"] < best_match.get(
                    "distance2", float("inf")
                ):
                    best_match = identify_result
                continue

            layer = identify_result.layer() if hasattr(identify_result, "layer") else None
            results = identify_result.results() if hasattr(identify_result, "results") else []
            for result in results:
                candidate_point = self._extractPlotPointFromResult(result)
                if candidate_point is None:
                    if fallback_match is None:
                        fallback_match = {
                            "layer": layer,
                            "result": result,
                            "plot_point": None,
                            "distance2": None,
                        }
                    continue
                dx = plot_point.x() - candidate_point.x()
                dy = plot_point.y() - candidate_point.y()
                distance2 = dx * dx + dy * dy
                if best_match is None or (
                    best_match["distance2"] is not None and distance2 < best_match["distance2"]
                ):
                    best_match = {
                        "layer": layer,
                        "result": result,
                        "plot_point": candidate_point,
                        "distance2": distance2,
                    }

        return best_match or fallback_match

    def _extractPlotPointFromResult(self, result):
        """Try to extract a plot point (distance/elevation) from a QVariantMap result."""
        if not result:
            return None

        profile_point = result.get("profilePoint") or result.get("profile_point")
        if profile_point is not None:
            return self._profilePointToPlotPoint(profile_point)

        if "distance" in result and "elevation" in result:
            return QPointF(float(result["distance"]), float(result["elevation"]))

        if "distance" in result and "z" in result:
            return QPointF(float(result["distance"]), float(result["z"]))

        if "x" in result and "y" in result:
            return QPointF(float(result["x"]), float(result["y"]))

        return None

    def _profilePointToPlotPoint(self, profile_point):
        """Convert QgsProfilePoint to QPointF (distance, elevation)."""
        if profile_point is None:
            return None
        distance = None
        elevation = None
        if hasattr(profile_point, "distance"):
            distance = (
                profile_point.distance()
                if callable(profile_point.distance)
                else profile_point.distance
            )
        if hasattr(profile_point, "elevation"):
            elevation = (
                profile_point.elevation()
                if callable(profile_point.elevation)
                else profile_point.elevation
            )
        if distance is None or elevation is None:
            return None
        return QPointF(float(distance), float(elevation))

    def _isEmptyProfilePoint(self, profile_point):
        if profile_point is None:
            return True
        if hasattr(profile_point, "isEmpty"):
            return profile_point.isEmpty()
        return False

    # ------------------------------------------------------------------
    # Tooltip
    # ------------------------------------------------------------------

    def _showHoverTooltip(self, plot_point, match):
        """Show or update the persistent custom tooltip."""
        should_hide = plot_point is None or match is None

        if should_hide:
            self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return

        text = self._formatHoverSummary(plot_point, match)
        if not text:
            self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return

        current_key = self._matchIdentityKey(match)
        same_feature = (
            self._last_hover_match is not None
            and self._matchIdentityKey(self._last_hover_match) == current_key
        )

        from qgis.PyQt.QtGui import QCursor

        current_pos = QCursor.pos()

        need_update = False
        if not self._last_tooltip_text:
            need_update = True
        elif not same_feature:
            need_update = True
        elif text != self._last_tooltip_text:
            need_update = True
        elif self._last_hover_global_pos:
            dx = abs(current_pos.x() - self._last_hover_global_pos.x())
            dy = abs(current_pos.y() - self._last_hover_global_pos.y())
            if dx > 50 or dy > 50:
                need_update = True

        if need_update:
            self._last_hover_global_pos = current_pos
            self._last_tooltip_text = text
            self._custom_tooltip.setTextFormat(Qt.TextFormat.RichText)
            self._custom_tooltip.setText(text)
            self._custom_tooltip.adjustSize()
            tooltip_pos = QPoint(current_pos.x() + 10, current_pos.y() + 10)
            self._custom_tooltip.move(tooltip_pos)
            self._custom_tooltip.show()
            self._custom_tooltip.raise_()

    def _formatHoverSummary(self, plot_point, match):
        """Build rich-text tooltip content for a hover match."""
        attrs, layer_name, _feature, _layer = self._attrsFromMatch(match)

        lines = []

        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name)
        is_manhole = self._isManholeHover(layer_name, attrs)

        if is_reach:
            obj_id = attrs.get(self.REACH_OBJ_ID)
            lines.append(f"Reach {obj_id}" if obj_id else "Reach")

            material = attrs.get(self.REACH_MATERIAL)
            if material is None or material == "":
                lines.append(f"Material: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Material: {material}")

            clear_height_mm = _to_float(attrs.get(self.REACH_CLEAR_HEIGHT))
            if clear_height_mm is None:
                lines.append(f"Clear height: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Clear height: {clear_height_mm:.0f} mm")

            length = _to_float(attrs.get(self.REACH_LENGTH_EFFECTIVE))
            if length is None:
                lines.append(f"Length: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Length: {self._formatMeters(length)}")

            slope_per_mill = _to_float(attrs.get(self.REACH_SLOPE_PER_MILL))
            if slope_per_mill is None:
                lines.append(f"Gradient: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Gradient: {slope_per_mill:.0f} \u2030")

            entry_level = _to_float(attrs.get(self.REACH_RP_FROM_LEVEL))
            if entry_level is None:
                lines.append(f"Entry level: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Entry level: {self._formatMeters(entry_level, decimals=1)}")

            exit_level = _to_float(attrs.get(self.REACH_RP_TO_LEVEL))
            if exit_level is None:
                lines.append(f"Exit level: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Exit level: {self._formatMeters(exit_level, decimals=1)}")

            if plot_point is not None:
                lines.append(f"Elevation at cursor: {plot_point.y():.2f} m")

        elif is_cover:
            cover_obj_id = attrs.get(self.COVER_OBJ_ID)
            lines.append(f"Cover {cover_obj_id}" if cover_obj_id else "Cover")

            level = _to_float(attrs.get(self.COVER_LEVEL))
            if level is None:
                lines.append(f"Level: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Level: {self._formatMeters(level, decimals=2)}")

            material = attrs.get(self.COVER_MATERIAL)
            if material is None or material == "":
                lines.append(f"Material: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Material: {material}")

            cover_shape = attrs.get(self.COVER_SHAPE)
            if cover_shape is None or cover_shape == "":
                lines.append(f"Cover shape: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Cover shape: {cover_shape}")

            brand = attrs.get(self.COVER_BRAND)
            if brand is None or brand == "":
                lines.append(f"Brand: {self.MISSING_DATA_HTML}")
            else:
                lines.append(f"Brand: {brand}")

        elif is_manhole:
            ws_type = str(attrs.get(self.MANHOLE_WS_TYPE) or "").lower()
            is_actual_manhole = ws_type == "manhole"
            obj_id = attrs.get(self.MANHOLE_OBJ_ID)

            if is_actual_manhole:
                lines.append(f"Manhole: {obj_id}" if obj_id else "Manhole")
                self._appendManholeLabelLine(
                    lines, "Cover level", attrs.get(self.MANHOLE_COVER_LABEL)
                )
                self._appendManholeLabelLine(
                    lines, "Bottom level", attrs.get(self.MANHOLE_BOTTOM_LABEL)
                )
                self._appendManholeLabelLine(
                    lines, "Entry level", attrs.get(self.MANHOLE_INPUT_LABEL)
                )
                self._appendManholeLabelLine(
                    lines, "Exit level", attrs.get(self.MANHOLE_OUTPUT_LABEL)
                )

                cover_level = self._manholeCoverLevelValue(attrs)
                bottom_level = self._manholeBottomLevelValue(attrs)
                if cover_level is not None and bottom_level is not None:
                    depth = cover_level - bottom_level
                    lines.append(f"Depth: {self._formatMeters(depth, decimals=2)}")
                else:
                    lines.append(f"Depth: {self.MISSING_DATA_HTML}")

                width_mm = _to_float(attrs.get(self.MANHOLE_DIMENSION1))
                if width_mm is None:
                    lines.append(f"Width: {self.MISSING_DATA_HTML}")
                else:
                    lines.append(f"Width: {width_mm:.0f} mm")
            else:
                lines.append(f"Node: {obj_id}" if obj_id else "Node")
                if ws_type:
                    lines.append(f"Type: {ws_type}")
                bottom_level = self._manholeBottomLevelValue(attrs)
                if bottom_level is None:
                    lines.append(f"Level: {self.MISSING_DATA_HTML}")
                else:
                    lines.append(f"Level: {bottom_level:.2f} m")
        else:
            if layer_name:
                lines.append(layer_name)

        if plot_point is not None and not is_reach and not is_cover and not is_manhole:
            lines.append(f"distance: {plot_point.x():.2f}")
            lines.append(f"elevation: {plot_point.y():.2f}")

        return "<br>".join(lines)

    # ------------------------------------------------------------------
    # Feature type detection
    # ------------------------------------------------------------------

    def _isReachHover(self, layer_name, attrs):
        layer_name_lower = (layer_name or "").lower()
        if "reach" in layer_name_lower:
            return True
        if "wastewater_node" in layer_name_lower or "manhole" in layer_name_lower:
            return False
        if "cover" in layer_name_lower:
            return False
        if "change_point" in layer_name_lower:
            return False
        return (
            attrs.get(self.REACH_MATERIAL) is not None
            and attrs.get(self.REACH_LENGTH_EFFECTIVE) is not None
        )

    def _isCoverHover(self, layer_name):
        layer_name_lower = (layer_name or "").lower()
        return "cover" in layer_name_lower and "wastewater_node" not in layer_name_lower

    def _isManholeHover(self, layer_name, attrs):
        if attrs and attrs.get("_is_manhole_dash"):
            return True
        layer_name_lower = (layer_name or "").lower()
        if "wastewater_node" in layer_name_lower:
            return True
        if "reach" in layer_name_lower:
            return False
        if "cover" in layer_name_lower and "wastewater" not in layer_name_lower:
            return False
        if "change_point" in layer_name_lower:
            return False
        return str(attrs.get(self.MANHOLE_WS_TYPE) or "").lower() == "manhole"

    # ------------------------------------------------------------------
    # Map highlight
    # ------------------------------------------------------------------

    def _highlightMatchOnMap(self, plot_point, match):
        """
        Highlight the hovered feature on the QGIS main map canvas.

        - Reach → highlight on vw_tww_reach by obj_id
        - Cover → highlight on vw_cover by co_obj_id
        - Manhole → highlight associated cover on vw_cover via fk_wastewater_structure
        """
        if self._map_canvas is None:
            return

        attrs, layer_name, _feature, _layer = self._attrsFromMatch(match)

        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name)
        is_manhole = self._isManholeHover(layer_name, attrs)

        if is_reach:
            obj_id = attrs.get(self.REACH_OBJ_ID)
            if not obj_id:
                self._clearHighlight()
                return
            highlight_key = f"reach:{obj_id}"
            if highlight_key == self._current_highlight_key:
                return
            self._doHighlightFeature(
                "vw_tww_reach", f'"obj_id" = \'{obj_id}\'', highlight_key
            )
        elif is_cover:
            cover_id = attrs.get(self.COVER_OBJ_ID)
            if not cover_id:
                self._clearHighlight()
                return
            highlight_key = f"cover:{cover_id}"
            if highlight_key == self._current_highlight_key:
                return
            self._doHighlightFeature(
                "vw_cover", f'"obj_id" = \'{cover_id}\'', highlight_key
            )
        elif is_manhole:
            ws_id = attrs.get(self.MANHOLE_OBJ_ID)
            if not ws_id:
                self._clearHighlight()
                return
            highlight_key = f"manhole:{ws_id}"
            if highlight_key == self._current_highlight_key:
                return
            self._doHighlightFeature(
                "vw_cover",
                f'"fk_wastewater_structure" = \'{ws_id}\'',
                highlight_key,
            )
        else:
            self._clearHighlight()

    def _doHighlightFeature(self, layer_name, filter_expr, highlight_key, fallback_layer=None):
        """
        Create a QgsHighlight on the map canvas for the first matching feature.

        :param layer_name: Name of the layer to query.
        :param filter_expr: QgsFeatureRequest filter expression string.
        :param highlight_key: Unique key to prevent duplicate highlights.
        :param fallback_layer: Fallback layer name if primary is not found.
        """
        map_layer = TwwLayerManager.layer(layer_name)
        if map_layer is None and fallback_layer:
            map_layer = TwwLayerManager.layer(fallback_layer)
        if map_layer is None:
            self._clearHighlight()
            return

        request = QgsFeatureRequest().setFilterExpression(filter_expr)
        request.setLimit(1)

        feat = None
        for f in map_layer.getFeatures(request):
            feat = f
            break

        if feat is None or feat.geometry() is None or feat.geometry().isEmpty():
            self._clearHighlight()
            return

        self._clearHighlight()
        self._current_highlight = QgsHighlight(self._map_canvas, feat.geometry(), map_layer)
        self._current_highlight.setColor(self.HIGHLIGHT_COLOR)
        self._current_highlight.setFillColor(self.HIGHLIGHT_FILL_COLOR)
        self._current_highlight.setBuffer(0.5)
        self._current_highlight.setMinWidth(2)
        self._current_highlight.setWidth(4)
        self._current_highlight.show()
        self._current_highlight_key = highlight_key

    def _clearHighlight(self):
        """Remove the current map canvas highlight."""
        if self._current_highlight is not None:
            self._current_highlight.hide()
            del self._current_highlight
            self._current_highlight = None
        self._current_highlight_key = None

    # ------------------------------------------------------------------
    # Attribute extraction helpers
    # ------------------------------------------------------------------

    def _attrsFromMatch(self, match):
        """Extract merged attributes, layer name, feature, and layer from a hover match."""
        result = match.get("result") if match else None
        layer = match.get("layer") if match else None
        layer_name = layer.name() if layer and hasattr(layer, "name") else ""
        feature = self._extractResultFeature(result, layer)
        result_attrs = self._extractResultAttributes(result)

        if feature:
            attrs = _feature_attributes(feature)
        else:
            attrs = dict(result_attrs)

        for key, value in result_attrs.items():
            if key not in attrs:
                attrs[key] = value

        return attrs, layer_name, feature, layer

    def _matchIdentityKey(self, match):
        """Stable identity for tooltip deduplication (layer feature or manhole dash)."""
        if not match:
            return None

        layer = match.get("layer")
        result = match.get("result")
        if layer is not None and hasattr(layer, "id"):
            feature = self._extractResultFeature(result, layer)
            if feature is not None and hasattr(feature, "id"):
                return ("layer", layer.id(), feature.id())
            if isinstance(result, dict):
                fid = result.get("featureId") or result.get("fid") or result.get("id")
                if fid is not None:
                    return ("layer", layer.id(), fid)
            return ("layer", layer.id(), None)

        if isinstance(result, dict):
            attrs = result.get("attributes") or {}
            if attrs.get("_is_manhole_dash"):
                obj_id = attrs.get("obj_id")
                if obj_id:
                    return ("dash", obj_id)
                distance = result.get("distance")
                if distance is not None:
                    return ("dash", distance)

        return None

    def _extractResultAttributes(self, result):
        """Extract attribute dict from a QgsElevationProfile identify result."""
        if not result:
            return {}
        if isinstance(result, dict):
            if "attributes" in result and isinstance(result["attributes"], dict):
                return dict(result["attributes"])
            if "feature" in result:
                feature = result.get("feature")
                attrs = _feature_attributes(feature)
                if attrs:
                    return attrs
            return {k: v for k, v in result.items() if not isinstance(v, (dict, list))}
        return {}

    def _extractResultFeature(self, result, layer):
        """Try to resolve the QgsFeature from an identify result."""
        if not result:
            return None
        if isinstance(result, dict):
            feature = result.get("feature")
            if feature is not None:
                return feature
            fid = result.get("featureId") or result.get("fid") or result.get("id")
            if fid is not None and layer is not None and hasattr(layer, "getFeature"):
                try:
                    return layer.getFeature(int(fid))
                except Exception:
                    try:
                        return layer.getFeature(fid)
                    except Exception:
                        return None
        return None

    # ------------------------------------------------------------------
    # Manhole tooltip helpers
    # ------------------------------------------------------------------

    def _manholeCoverLevelValue(self, attrs):
        return _to_float(attrs.get(self.MANHOLE_CO_LEVEL))

    def _manholeBottomLevelValue(self, attrs):
        level = _to_float(attrs.get(self.MANHOLE_BOTTOM_LEVEL))
        if level == 0:
            return None
        return level

    def _appendManholeLabelLine(self, lines, title, label_value):
        text = self._formatLevelLabel(label_value)
        if not text:
            lines.append(f"{title}: {self.MISSING_DATA_HTML}")
        else:
            lines.append(f"{title}: {text}")

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _parseLevelLabel(self, label):
        """
        Parse a DB level label into a list of (index, value) tuples.

        The DB stores pre-formatted strings like '\\nC=3401.00' or
        '\\nI1=2736.50\\nI2=2736.50'. We strip the letter prefix and keep
        only the numeric value; a trailing digit on the prefix (I1, I2, …)
        is captured as the index.
        """
        if label is None:
            return []
        entries = []
        for raw in str(label).replace("\r", "\n").split("\n"):
            token = raw.strip()
            if not token:
                continue
            if "=" in token:
                prefix, value = token.split("=", 1)
                prefix = prefix.strip()
                value = value.strip()
                cut = len(prefix)
                while cut > 0 and prefix[cut - 1].isdigit():
                    cut -= 1
                entries.append((prefix[cut:], value))
            else:
                entries.append(("", token))
        return entries

    def _formatLevelLabel(self, label):
        """
        Format a level label inline (no line breaks).

        Single value → just the number; multiple values → '1: v1; 2: v2'.
        """
        entries = self._parseLevelLabel(label)
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0][1]
        parts = []
        for i, (idx, value) in enumerate(entries):
            parts.append(f"{idx or (i + 1)}: {value}")
        return "; ".join(parts)

    def _formatMeters(self, value, decimals=2):
        if value is None:
            return None
        try:
            return f"{float(value):.{decimals}f} m"
        except (TypeError, ValueError):
            return str(value)
