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
from qgis.PyQt.QtWidgets import QLabel, QToolTip

from ...utils.twwlayermanager import TwwLayerManager
from .layer_setup import _feature_attributes, _pick_attr, _to_float


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
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
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
        if hasattr(self._canvas, "canvasPointHovered"):
            self._canvas.canvasPointHovered.connect(self._onCanvasPointHovered)

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
        QToolTip.hideText()
        self._custom_tooltip.hide()
        self._clearHighlight()

    # ------------------------------------------------------------------
    # Internal hover pipeline
    # ------------------------------------------------------------------

    def _onCanvasPointHovered(self, _map_point, profile_point):
        """Handle hover signal from QgsElevationProfileCanvas (official API)."""
        if not self._hover_enabled:
            return
        self._updateHoverMatch(profile_point)

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
        Identify custom-drawn manhole dashes (not in layers, drawn via drawForeground).

        :param plot_point: QPointF with (distance, elevation) in plot coordinates.
        :return: Fake identify-result dict, or None if no match.
        """
        if not hasattr(self._canvas, "_manhole_dashes") or not self._canvas._manhole_dashes:
            return None

        distance = plot_point.x()
        elevation = plot_point.y()

        tolerance_dist = 5.0
        tolerance_elev = 5.0

        best_match = None
        best_distance2 = float("inf")

        for dash in self._canvas._manhole_dashes:
            dash_distance = dash.get("distance")
            cover_level = dash.get("cover_level")
            bottom_level = dash.get("bottom_level")
            width_px = dash.get("width", 10)

            if dash_distance is None or cover_level is None or bottom_level is None:
                continue

            dist_diff = abs(distance - dash_distance)
            if dist_diff > tolerance_dist:
                continue

            min_elev = min(bottom_level, cover_level) - tolerance_elev
            max_elev = max(bottom_level, cover_level) + tolerance_elev

            if not (min_elev <= elevation <= max_elev):
                continue

            center_elev = (cover_level + bottom_level) / 2
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
                            "cover_level": cover_level,
                            "bottom_level": bottom_level,
                            "bottom_level_missing": dash.get("bottom_level_missing", False),
                            "width": width_px,
                            "node_type": "manhole",
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
            QToolTip.hideText()
            self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return

        text = self._formatHoverSummary(plot_point, match)
        if not text:
            QToolTip.hideText()
            self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return

        current_layer = match.get("layer_name", "")
        current_fid = match.get("feature_id", "")
        same_feature = False

        if self._last_hover_match:
            last_layer = self._last_hover_match.get("layer_name", "")
            last_fid = self._last_hover_match.get("feature_id", "")
            same_feature = current_layer == last_layer and current_fid == last_fid

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
            self._custom_tooltip.setTextFormat(Qt.RichText)
            self._custom_tooltip.setText(text)
            self._custom_tooltip.adjustSize()
            tooltip_pos = QPoint(current_pos.x() + 10, current_pos.y() + 10)
            self._custom_tooltip.move(tooltip_pos)
            self._custom_tooltip.show()
            self._custom_tooltip.raise_()

    def _formatHoverSummary(self, plot_point, match):
        """Build rich-text tooltip content for a hover match."""
        result = match.get("result") if match else None
        layer = match.get("layer") if match else None
        layer_name = layer.name() if layer and hasattr(layer, "name") else ""
        feature = self._extractResultFeature(result, layer)
        result_attrs = self._extractResultAttributes(result)

        if feature:
            attrs = _feature_attributes(feature)
        else:
            attrs = result_attrs

        if result_attrs:
            for key in ["distance", "elevation", "delta"]:
                if key in result_attrs and key not in attrs:
                    attrs[key] = result_attrs[key]

        lines = []

        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name, attrs)
        is_manhole = self._isManholeHover(layer_name, attrs)

        if is_reach:
            obj_id = _pick_attr(attrs, ["obj_id", "objId", "id", "reach_id"])
            lines.append(f"Reach {obj_id}" if obj_id else "Reach")
            material = _pick_attr(
                attrs,
                ["material_abbr_en", "material_abbr_de", "material_abbr_fr", "material"],
            )
            self._appendLabeled(lines, "Material", material)
            width_mm = _to_float(_pick_attr(attrs, ["clear_height", "width", "diameter"]))
            if width_mm is not None:
                lines.append(f"Width: {width_mm:.0f} mm")
            length = _to_float(
                _pick_attr(attrs, ["ch_pipe_length", "length_effective", "length_full", "length"])
            )
            if length is None:
                length = self._lengthFromFeature(feature)
            self._appendLabeled(lines, "Length", self._formatMeters(length))
            gradient = self._deriveGradient(attrs, length)
            if gradient is not None:
                lines.append(f"Gradient: {gradient * 1000:.0f} \u2030")
            entry_level = _to_float(
                _pick_attr(attrs, ["rp_from_level", "from_level", "start_level", "startLevel"])
            )
            exit_level = _to_float(
                _pick_attr(attrs, ["rp_to_level", "to_level", "end_level", "endLevel"])
            )
            if entry_level is None or exit_level is None:
                start_z, end_z = self._levelsFromFeatureGeometry(feature)
                if entry_level is None:
                    entry_level = start_z
                if exit_level is None:
                    exit_level = end_z
            self._appendLabeled(lines, "Entry level", self._formatMeters(entry_level, decimals=1))
            self._appendLabeled(lines, "Exit level", self._formatMeters(exit_level, decimals=1))
            if plot_point is not None and entry_level is not None and exit_level is not None:
                lines.append(f"Elevation at cursor: {plot_point.y():.2f} m")

        elif is_cover:
            obj_id = _pick_attr(attrs, ["obj_id", "objId", "id"])
            lines.append(f"Cover {obj_id}" if obj_id else "Cover")
            cover_data = self._getCoverEnhancedData(obj_id, attrs)
            level = cover_data.get("level") or _to_float(
                _pick_attr(attrs, ["level", "cover_level"])
            )
            self._appendLabeled(lines, "Level", self._formatMeters(level, decimals=2))
            self._appendLabeled(
                lines, "Material", cover_data.get("material") or _pick_attr(attrs, ["material"])
            )
            self._appendLabeled(
                lines,
                "Cover shape",
                cover_data.get("cover_shape") or _pick_attr(attrs, ["cover_shape"]),
            )
            self._appendLabeled(
                lines, "Brand", cover_data.get("brand") or _pick_attr(attrs, ["brand"])
            )

        elif is_manhole:
            node_type = str(_pick_attr(attrs, ["node_type", "nodeType", "type"]) or "").lower()
            is_actual_manhole = "manhole" in node_type
            obj_id = _pick_attr(attrs, ["obj_id", "objId", "id", "ws_obj_id"])

            if is_actual_manhole:
                lines.append(f"Manhole: {obj_id}" if obj_id else "Manhole")
                manhole_data = self._getManholeEnhancedData(obj_id, attrs, feature)

                cover_level = manhole_data.get("cover_level")
                self._appendLabeled(
                    lines, "Cover level", self._formatMeters(cover_level, decimals=2)
                )

                bottom_level = manhole_data.get("bottom_level")
                bottom_level_missing = (
                    manhole_data.get("bottom_level_missing", False)
                    or attrs.get("bottom_level_missing", False)
                    or bottom_level is None
                    or bottom_level == 0
                )
                if bottom_level_missing:
                    lines.append('Bottom level: <span style="color:red">Missing Data</span>')
                else:
                    self._appendLabeled(
                        lines, "Bottom level", self._formatMeters(bottom_level, decimals=2)
                    )

                entry_level = manhole_data.get("entry_level")
                self._appendLabeled(
                    lines, "Entry level", self._formatMeters(entry_level, decimals=2)
                )
                exit_level = manhole_data.get("exit_level")
                self._appendLabeled(
                    lines, "Exit level", self._formatMeters(exit_level, decimals=2)
                )
                if cover_level is not None and bottom_level is not None:
                    depth = cover_level - bottom_level
                    self._appendLabeled(lines, "Depth", self._formatMeters(depth, decimals=2))
                width = manhole_data.get("width")
                if width is not None:
                    lines.append(f"Width: {width:.0f} mm")
            else:
                lines.append(f"Node: {obj_id}" if obj_id else "Node")
                if node_type:
                    lines.append(f"Type: {node_type}")
                bottom_level = _to_float(
                    _pick_attr(attrs, ["bottom_level", "bottomLevel", "level", "invert_level"])
                )
                if bottom_level is None or bottom_level == 0:
                    lines.append('Bottom level: <span style="color:red">Missing Data</span>')
                elif bottom_level is not None:
                    lines.append(f"Level: {bottom_level:.2f} m")
                elif plot_point is not None:
                    lines.append(f"Elevation: {plot_point.y():.2f} m")
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
        has_material = _pick_attr(attrs, ["material"]) is not None
        has_length = _pick_attr(attrs, ["length_full", "length"]) is not None
        return has_material and has_length

    def _isCoverHover(self, layer_name, attrs):
        layer_name_lower = (layer_name or "").lower()
        return "cover" in layer_name_lower and "wastewater_node" not in layer_name_lower

    def _isManholeHover(self, layer_name, attrs):
        if attrs and attrs.get("_is_manhole_dash"):
            return True
        layer_name_lower = (layer_name or "").lower()
        if "wastewater_node" in layer_name_lower or "manhole" in layer_name_lower:
            return True
        if "reach" in layer_name_lower:
            return False
        if "cover" in layer_name_lower and "wastewater" not in layer_name_lower:
            return False
        if "change_point" in layer_name_lower:
            return False
        node_type = str(_pick_attr(attrs, ["node_type", "nodeType", "type"]) or "").lower()
        if "manhole" in node_type:
            return True
        has_cover = _pick_attr(attrs, ["cover_level"]) is not None
        has_bottom = _pick_attr(attrs, ["bottom_level"]) is not None
        return has_cover and has_bottom

    # ------------------------------------------------------------------
    # Data enrichment (layer queries)
    # ------------------------------------------------------------------

    def _getCoverEnhancedData(self, obj_id, attrs):
        """
        Get enhanced cover data from vm_cover layer.

        Fields: obj_id, level, material, cover_shape, brand.
        """
        result = {}
        if not obj_id:
            return result

        cover_layer = TwwLayerManager.layer("vm_cover") or TwwLayerManager.layer("vw_cover")
        if cover_layer is None:
            result["level"] = _to_float(_pick_attr(attrs, ["level"]))
            result["material"] = _pick_attr(attrs, ["material"])
            result["cover_shape"] = _pick_attr(attrs, ["cover_shape"])
            result["brand"] = _pick_attr(attrs, ["brand"])
            return result

        for feat in cover_layer.getFeatures():
            feat_attrs = _feature_attributes(feat)
            if str(_pick_attr(feat_attrs, ["obj_id", "objId", "id"])) == str(obj_id):
                result["level"] = _to_float(_pick_attr(feat_attrs, ["level"]))
                result["material"] = _pick_attr(feat_attrs, ["material"])
                result["cover_shape"] = _pick_attr(feat_attrs, ["cover_shape"])
                result["brand"] = _pick_attr(feat_attrs, ["brand"])
                break

        return result

    def _getManholeEnhancedData(self, obj_id, attrs, feature):
        """
        Get enhanced manhole data from related tables.

        Queries: vw_tww_wastewater_structure, vm_cover/vw_cover, vw_wastewater_node.

        TODO (Performance): Replace full-table iteration with filtered QgsFeatureRequest:
            request = QgsFeatureRequest().setFilterExpression(f'"obj_id" = \\'{obj_id}\\'')
            request.setLimit(1)
        See profile-dev-guide SKILL.md §3.2 for details.
        """
        result = {}
        ws_id = _pick_attr(
            attrs,
            [
                "fk_wastewater_structure",
                "fk_wastewater_structure_obj_id",
                "ws_obj_id",
                "fk_wastewater_structure_id",
                "obj_id",
            ],
        )
        if not ws_id:
            ws_id = obj_id

        # 1. Query vw_tww_wastewater_structure for entry/exit labels
        ws_layer = TwwLayerManager.layer("vw_tww_wastewater_structure") or TwwLayerManager.layer(
            "tww_wastewater_structure"
        )
        if ws_layer and ws_id:
            for ws_feat in ws_layer.getFeatures():
                ws_attrs = _feature_attributes(ws_feat)
                if str(_pick_attr(ws_attrs, ["obj_id", "objId", "id"])) == str(ws_id):
                    result["entry_level"] = _to_float(
                        _pick_attr(ws_attrs, ["_input_label", "input_label", "entry_level"])
                    )
                    result["exit_level"] = _to_float(
                        _pick_attr(ws_attrs, ["_output_label", "output_label", "exit_level"])
                    )
                    break

        # 2. Query cover level from vm_cover or vw_cover
        cover_layer = TwwLayerManager.layer("vm_cover") or TwwLayerManager.layer("vw_cover")
        if cover_layer and ws_id:
            for cover_feat in cover_layer.getFeatures():
                cover_attrs = _feature_attributes(cover_feat)
                cover_ws_id = _pick_attr(
                    cover_attrs,
                    [
                        "fk_wastewater_structure",
                        "fk_wastewater_structure_obj_id",
                        "ws_obj_id",
                        "fk_wastewater_structure_id",
                    ],
                )
                if str(cover_ws_id) == str(ws_id):
                    result["cover_level"] = _to_float(
                        _pick_attr(
                            cover_attrs, ["level", "cover_level", "coverLevel", "elevation"]
                        )
                    )
                    break

        if "cover_level" not in result or result["cover_level"] is None:
            result["cover_level"] = _to_float(_pick_attr(attrs, ["cover_level", "coverLevel"]))

        # 3. Query bottom level from vw_wastewater_node
        node_layer = TwwLayerManager.layer("vw_wastewater_node")
        if node_layer and obj_id:
            for node_feat in node_layer.getFeatures():
                node_attrs = _feature_attributes(node_feat)
                if str(_pick_attr(node_attrs, ["obj_id", "objId", "id"])) == str(obj_id):
                    result["bottom_level"] = _to_float(
                        _pick_attr(node_attrs, ["bottom_level", "bottomLevel", "invert_level"])
                    )
                    result["width"] = _to_float(
                        _pick_attr(node_attrs, ["dimension1", "width", "diameter"])
                    )
                    break

        if "bottom_level" not in result or result["bottom_level"] is None:
            result["bottom_level"] = _to_float(
                _pick_attr(attrs, ["bottom_level", "bottomLevel", "invert_level"])
            )

        # 4. Fallback for missing bottom_level
        if result.get("bottom_level") is None or result.get("bottom_level") == 0:
            entry_lv = result.get("entry_level")
            exit_lv = result.get("exit_level")
            candidates = [v for v in (entry_lv, exit_lv) if v is not None]
            if candidates:
                result["bottom_level"] = min(candidates)
                result["bottom_level_missing"] = True
            else:
                reach_layer = TwwLayerManager.layer("vw_tww_reach")
                if reach_layer is not None and obj_id:
                    reach_levels = []
                    for reach_feat in reach_layer.getFeatures():
                        reach_attrs = _feature_attributes(reach_feat)
                        to_node = _pick_attr(
                            reach_attrs, ["rp_to_fk_wastewater_networkelement"]
                        )
                        from_node = _pick_attr(
                            reach_attrs, ["rp_from_fk_wastewater_networkelement"]
                        )
                        if str(to_node) == str(obj_id):
                            lv = _to_float(_pick_attr(reach_attrs, ["rp_to_level"]))
                            if lv is not None:
                                reach_levels.append(lv)
                        if str(from_node) == str(obj_id):
                            lv = _to_float(_pick_attr(reach_attrs, ["rp_from_level"]))
                            if lv is not None:
                                reach_levels.append(lv)
                    if reach_levels:
                        result["bottom_level"] = min(reach_levels)
                result["bottom_level_missing"] = True
        else:
            result["bottom_level_missing"] = False

        return result

    # ------------------------------------------------------------------
    # Map highlight
    # ------------------------------------------------------------------

    def _highlightMatchOnMap(self, plot_point, match):
        """
        Highlight the hovered feature on the QGIS main map canvas.

        - Reach → highlight on vw_tww_reach by obj_id
        - Cover → highlight on vm_cover by obj_id
        - Manhole → highlight associated cover on vm_cover via fk_wastewater_structure
        """
        if self._map_canvas is None:
            return

        result = match.get("result") if match else None
        layer = match.get("layer") if match else None
        layer_name = layer.name() if layer and hasattr(layer, "name") else ""
        feature = self._extractResultFeature(result, layer)
        result_attrs = self._extractResultAttributes(result)

        attrs = _feature_attributes(feature) if feature else result_attrs
        if result_attrs:
            for key in result_attrs:
                if key not in attrs:
                    attrs[key] = result_attrs[key]

        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name, attrs)
        is_manhole = self._isManholeHover(layer_name, attrs)
        obj_id = _pick_attr(attrs, ["obj_id", "objId", "id"])

        if not obj_id and not is_manhole:
            self._clearHighlight()
            return

        if is_reach:
            highlight_key = f"reach:{obj_id}"
            if highlight_key == self._current_highlight_key:
                return
            self._doHighlightFeature(
                "vw_tww_reach", f'"obj_id" = \'{obj_id}\'', highlight_key
            )
        elif is_cover:
            highlight_key = f"cover:{obj_id}"
            if highlight_key == self._current_highlight_key:
                return
            self._doHighlightFeature(
                "vm_cover", f'"obj_id" = \'{obj_id}\'', highlight_key, "vw_cover"
            )
        elif is_manhole:
            ws_id = _pick_attr(
                attrs, ["fk_wastewater_structure", "fk_wastewater_structure_obj_id", "ws_obj_id"]
            )
            if not ws_id and obj_id:
                node_layer = TwwLayerManager.layer("vw_wastewater_node")
                if node_layer:
                    req = QgsFeatureRequest().setFilterExpression(f'"obj_id" = \'{obj_id}\'')
                    req.setLimit(1)
                    for nf in node_layer.getFeatures(req):
                        na = _feature_attributes(nf)
                        ws_id = _pick_attr(na, ["fk_wastewater_structure", "ws_obj_id"])
                        break
            if ws_id:
                highlight_key = f"manhole:{ws_id}"
                if highlight_key == self._current_highlight_key:
                    return
                self._doHighlightFeature(
                    "vm_cover",
                    f'"fk_wastewater_structure" = \'{ws_id}\'',
                    highlight_key,
                    "vw_cover",
                )
            else:
                self._clearHighlight()
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
    # Geometry / math helpers
    # ------------------------------------------------------------------

    def _lengthFromFeature(self, feature):
        if feature is None or not hasattr(feature, "geometry"):
            return None
        try:
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                return None
            return float(geometry.length())
        except Exception:
            return None

    def _levelsFromFeatureGeometry(self, feature):
        if feature is None or not hasattr(feature, "geometry"):
            return (None, None)
        try:
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                return (None, None)
            points = geometry.asPolyline()
            if not points:
                multi = geometry.asMultiPolyline()
                if multi:
                    first = multi[0]
                    last = multi[-1]
                    if first:
                        points = [first[0]]
                    if last:
                        points.append(last[-1])
            if not points:
                return (None, None)
            return (self._pointZ(points[0]), self._pointZ(points[-1]))
        except Exception:
            return (None, None)

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

    def _deriveGradient(self, attrs, length):
        gradient = _to_float(_pick_attr(attrs, ["_slope_per_mill", "gradient", "slope"]))
        if gradient is not None:
            if abs(gradient) < 1:
                return gradient
            return gradient / 1000
        if length is None or length == 0:
            return None
        from_level = _to_float(
            _pick_attr(attrs, ["rp_from_level", "from_level", "start_level", "startLevel"])
        )
        to_level = _to_float(
            _pick_attr(attrs, ["rp_to_level", "to_level", "end_level", "endLevel"])
        )
        if from_level is None or to_level is None:
            return None
        return (from_level - to_level) / float(length)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _formatMeters(self, value, decimals=2):
        if value is None:
            return None
        try:
            return f"{float(value):.{decimals}f} m"
        except (TypeError, ValueError):
            return str(value)

    def _appendLabeled(self, lines, label, value):
        if value is None or value == "":
            return
        lines.append(f"{label}: {value}")
