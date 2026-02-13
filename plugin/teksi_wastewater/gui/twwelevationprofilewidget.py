# -----------------------------------------------------------
#
# Elevation Profile Widget
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
    QgsFeatureRequest,
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
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QCursor, QPainter, QPen
from qgis.PyQt.QtWidgets import QToolTip, QVBoxLayout, QWidget, QLabel
from qgis.gui import QgsElevationProfileCanvas, QgsHighlight

from ..tools.twwnetwork import TwwGraphManager
from ..utils.twwlayermanager import TwwLayerManager


class TwwElevationProfileCanvas(QgsElevationProfileCanvas):
    """
    Custom elevation profile canvas to ensure mouse move events reach hover logic.
    """

    def __init__(self, parent=None, hover_callback=None, leave_callback=None):
        super().__init__(parent)
        self._hover_callback = hover_callback
        self._leave_callback = leave_callback
        self._manhole_dashes = []
        self._manhole_shaft_color = QColor("#6E4C1E")  # Brown color for manhole shaft walls
        self._manhole_cover_color = QColor("#2C3E50")  # Dark gray for manhole cover
        self._manhole_default_width = 1000  # Default manhole diameter in mm (1m)
        self._manhole_default_px_width = 10  # Default pixel width for manhole shaft
        self.setMouseTracking(True)
        if hasattr(self, "viewport"):
            try:
                self.viewport().setMouseTracking(True)
            except Exception:
                pass

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._hover_callback:
            self._hover_callback(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._leave_callback:
            self._leave_callback(event)

    def setManholeDashes(self, dashes):
        self._manhole_dashes = dashes or []
        self.update()

    def _plotPointToCanvasPoint(self, plot_point):
        if plot_point is None or not hasattr(self, "plotPointToCanvasPoint"):
            return None
        try:
            return self.plotPointToCanvasPoint(plot_point)
        except TypeError:
            try:
                from qgis.core import QgsProfilePoint

                if isinstance(plot_point, QPointF):
                    profile_point = QgsProfilePoint(float(plot_point.x()), float(plot_point.y()))
                else:
                    profile_point = QgsProfilePoint(float(plot_point[0]), float(plot_point[1]))
                return self.plotPointToCanvasPoint(profile_point)
            except Exception:
                return None

    def drawForeground(self, painter, rect):
        """
        Override drawForeground to draw custom manhole dashes.
        Reach lines are now rendered via QGIS native API using temp layer with Z values.
        """
        super().drawForeground(painter, rect)
        
        # Safety checks
        if not hasattr(self, "plotPointToCanvasPoint"):
            return
        if not self._manhole_dashes:
            return
        if painter is None or not painter.isActive():
            return
        
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            plot_area = self.plotArea() if hasattr(self, "plotArea") else None
            
            # Draw manhole shafts (double-line rectangle + cover line)
            for dash in self._manhole_dashes:
                distance = dash.get("distance")
                cover_level = dash.get("cover_level")
                bottom_level = dash.get("bottom_level")
                shaft_width_px = dash.get("width", self._manhole_default_px_width)
                
                if distance is None or cover_level is None or bottom_level is None:
                    continue
                
                # Convert plot coordinates to canvas coordinates
                cover_canvas = self._plotPointToCanvasPoint(QPointF(float(distance), float(cover_level)))
                bottom_canvas = self._plotPointToCanvasPoint(QPointF(float(distance), float(bottom_level)))
                if cover_canvas is None or bottom_canvas is None:
                    continue
                
                cover_pt = QPointF(cover_canvas.x(), cover_canvas.y())
                bottom_pt = QPointF(bottom_canvas.x(), bottom_canvas.y())
                
                if plot_area is not None:
                    if not plot_area.contains(cover_pt) and not plot_area.contains(bottom_pt):
                        continue
                
                half_width = shaft_width_px / 2.0
                
                # Draw left shaft wall (vertical line)
                left_top = QPointF(cover_pt.x() - half_width, cover_pt.y())
                left_bottom = QPointF(bottom_pt.x() - half_width, bottom_pt.y())
                
                # Draw right shaft wall (vertical line)
                right_top = QPointF(cover_pt.x() + half_width, cover_pt.y())
                right_bottom = QPointF(bottom_pt.x() + half_width, bottom_pt.y())
                
                # Shaft wall pen (brown, 1.5px)
                shaft_pen = QPen(self._manhole_shaft_color, 1.5)
                shaft_pen.setStyle(Qt.SolidLine)
                shaft_pen.setCapStyle(Qt.FlatCap)
                painter.setPen(shaft_pen)
                
                # Draw shaft walls
                painter.drawLine(left_top, left_bottom)
                painter.drawLine(right_top, right_bottom)
                
                # Draw bottom line (connect left and right at bottom)
                painter.drawLine(left_bottom, right_bottom)
                
                # Draw cover line (horizontal line at top, slightly wider)
                cover_pen = QPen(self._manhole_cover_color, 2.5)
                cover_pen.setStyle(Qt.SolidLine)
                cover_pen.setCapStyle(Qt.FlatCap)
                painter.setPen(cover_pen)
                
                cover_left = QPointF(cover_pt.x() - half_width - 3, cover_pt.y())
                cover_right = QPointF(cover_pt.x() + half_width + 3, cover_pt.y())
                painter.drawLine(cover_left, cover_right)
        except Exception:
            # Silently ignore drawing errors to prevent crashes
            pass


class TwwElevationProfileWidget(QWidget):
    """
    Widget that wraps QGIS Elevation Profile Canvas for displaying wastewater network profiles.
    
    This widget replaces the old TwwPlotSVGWidget which used QtWebKit.
    """

    # Highlight color for hover feedback on the main map canvas
    HIGHLIGHT_COLOR = QColor("#2ECC71")  # Emerald green
    HIGHLIGHT_FILL_COLOR = QColor(46, 204, 113, 60)  # Semi-transparent fill

    def __init__(self, parent, network_analyzer: TwwGraphManager = None):
        """
        Initialize the elevation profile widget.
        
        :param parent: Parent widget
        :param network_analyzer: Network analyzer instance (kept for compatibility, may not be used)
        """
        QWidget.__init__(self, parent)
        
        # Create the layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create the QGIS Elevation Profile Canvas
        self.canvas = TwwElevationProfileCanvas(
            self, self._onCanvasMouseMove, self._onCanvasLeave
        )
        layout.addWidget(self.canvas)
        
        # Store network analyzer for potential future use
        self.networkAnalyzer = network_analyzer
        
        # Vertical exaggeration value (default 10x)
        self.verticalExaggeration = 10.0
        
        # Track if data sources have been set up
        self._data_sources_setup = False
        self._profile_curve_geom = None
        self._manhole_dash_tolerance = 10.0
        self._temp_reach_layer = None  # Temporary layer with Z values

        # Hover state for profile canvas (used by the new elevation profile API)
        self._hover_enabled = True
        self._hover_snap_px = 200  # Allow 200px mouse movement without hiding tooltip (canvas shows km range)
        self._last_hover_match = None
        self._last_hover_pos = None
        self._last_hover_global_pos = None
        self._last_tooltip_text = None  # Track last tooltip text to avoid unnecessary updates

        # Create custom persistent tooltip label (instead of using QToolTip)
        self._custom_tooltip = QLabel(None)
        self._custom_tooltip.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._custom_tooltip.setStyleSheet("""
            QLabel {
                background-color: #ffffcc;
                border: 1px solid #000000;
                padding: 4px;
                color: #000000;
                font-family: monospace;
                font-size: 10pt;
            }
        """)
        self._custom_tooltip.hide()
        self._profile_generation_complete = False

        # Map canvas highlight for hover feedback
        try:
            from qgis.utils import iface as _iface
            self._map_canvas = _iface.mapCanvas() if _iface else None
        except Exception:
            self._map_canvas = None
        self._current_highlight = None
        self._current_highlight_key = None
        self._setupHoverHandling()

        # Clean up highlight when project is closed/cleared
        QgsProject.instance().cleared.connect(self._clearHighlight)
        
        # Monitor profile generation completion
        if hasattr(self.canvas, 'activeJobCountChanged'):
            self.canvas.activeJobCountChanged.connect(self._onJobCountChanged)
    
    def cleanup(self):
        """
        Clean up resources. Must be called before the widget is destroyed
        to ensure QgsHighlight is removed from the map canvas.
        """
        self._clearHighlight()
        try:
            QgsProject.instance().cleared.disconnect(self._clearHighlight)
        except Exception:
            pass

    def changeVerticalExaggeration(self, val):
        """
        Change the vertical exaggeration of the profile.
        
        This method is called by TwwProfileDockWidget when the user adjusts the slider.
        
        :param val: Vertical exaggeration value (e.g., 10 for 10x)
        """
        self.verticalExaggeration = float(val)
        # TODO: Apply vertical exaggeration to canvas
        # Note: QgsElevationProfileCanvas uses axisScaleRatio() which is read-only
        # We may need to investigate how to set vertical exaggeration
        # For now, we just store the value
    
    def printProfile(self):
        """
        Print the profile to PDF.
        
        This method is called by TwwProfileDockWidget when the user clicks the print button.
        """
        # TODO: Implement printing functionality
        # This can be done by rendering the canvas to an image/PDF
        pass

        
    
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
        # Get CRS from original layer
        crs = original_layer.crs()
        crs_string = crs.authid() if crs.isValid() else "EPSG:2056"
        
        # Create memory layer with LineStringZ geometry
        mem_layer = QgsVectorLayer(
            f"LineStringZ?crs={crs_string}",
            "reach_with_z",
            "memory"
        )
        provider = mem_layer.dataProvider()
        
        # Copy fields from original layer
        provider.addAttributes(original_layer.fields().toList())
        mem_layer.updateFields()
        
        # Copy features with corrected Z values
        features = []
        skipped_no_level = 0
        skipped_no_vertices = 0
        
        for feat in original_layer.getFeatures():
            # Get elevation from attributes
            from_level = None
            to_level = None
            
            try:
                from_level = feat['rp_from_level']
                to_level = feat['rp_to_level']
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
            
            # Get original geometry vertices
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            
            vertices = list(geom.vertices())
            if len(vertices) < 2:
                skipped_no_vertices += 1
                continue
            
            # Create new geometry with Z values
            # For simplicity, we create a 2-point line with start and end Z values
            # If the original has more vertices, we interpolate Z values
            new_points = []
            total_length = geom.length()
            
            if total_length > 0 and len(vertices) > 2:
                # Interpolate Z values for intermediate vertices
                accumulated_length = 0.0
                for i, vertex in enumerate(vertices):
                    if i == 0:
                        z_value = from_level
                    elif i == len(vertices) - 1:
                        z_value = to_level
                    else:
                        # Calculate distance ratio and interpolate Z
                        prev_vertex = vertices[i - 1]
                        segment_length = QgsPointXY(prev_vertex.x(), prev_vertex.y()).distance(
                            QgsPointXY(vertex.x(), vertex.y())
                        )
                        accumulated_length += segment_length
                        ratio = accumulated_length / total_length
                        z_value = from_level + (to_level - from_level) * ratio
                    
                    new_points.append(QgsPoint(vertex.x(), vertex.y(), z_value))
            else:
                # Simple 2-point line
                new_points.append(QgsPoint(vertices[0].x(), vertices[0].y(), from_level))
                new_points.append(QgsPoint(vertices[-1].x(), vertices[-1].y(), to_level))
            
            # Create new feature
            new_line = QgsLineString(new_points)
            new_geom = QgsGeometry(new_line)
            
            new_feat = QgsFeature()
            new_feat.setGeometry(new_geom)
            new_feat.setAttributes(feat.attributes())
            features.append(new_feat)
        
        provider.addFeatures(features)
        mem_layer.updateExtents()
        return mem_layer

    def setupDataSources(self):
        """
        Set up data sources for the elevation profile canvas.
        
        Based on the working configuration tested by the project owner:
        - Use "Capture curve from features" approach
        - Selected layers: vw_tww_reach, vw_wastewater_node, vw_cover, vw_change_points
        - Set all layers to "Absolute" clamping (use Z values from geometry)
        
        Reference: https://docs.qgis.org/3.40/en/docs/user_manual/map_views/elevation_profile.html
        """
        # 1. CRITICAL: Set project first - this is required by QgsElevationProfileCanvas
        project = QgsProject.instance()
        self.canvas.setProject(project)
        # 2. Define the layers to use (based on working configuration)
        # These are the layers that were tested and confirmed to work with "Absolute" setting
        # profile_type: 'surface' = Continuous Surface, 'features' = Individual Features
        # Style: (line_color, line_width, fill_color, marker_color, marker_size)
        # Color scheme designed for professional engineering drawings
        # - Deep blue for pipes/reaches (main infrastructure) - hollow style
        # - Purple for wastewater nodes
        # - Green for covers (ground level markers)
        # - Red for change points (highlight important transitions)
        layer_configs = [
            ('vw_tww_reach', 'Reach/Pipe segments', 'features', 
             {'line': '#1A5276', 'line_width': 4.0, 'fill': '#1A527620', 'marker': '#5DADE2',
              'marker_size': 4, 'marker_outline': '#1A5276', 'marker_name': 'circle',
              'hollow': True, 'line_inner': '#E8F6FF', 'outline_width': 1.0}),
            ('vw_wastewater_node', 'Wastewater nodes', 'features',
             {'line': '#8E44AD', 'line_width': 1.0, 'fill': '#8E44AD30', 'marker': '#8E44AD', 'marker_size': 5}),
            ('vw_cover', 'Covers', 'features',
             {'line': '#00000000', 'line_width': 0.1, 'fill': '#00000000', 'marker': '#27AE60', 'marker_size': 6,
              'marker_outline': '#1E8449', 'marker_name': 'circle'}),
            ('vw_change_points', 'Change points', 'features',
             {'line': '#E74C3C', 'line_width': 1.5, 'fill': '#E74C3C20', 'marker': '#E74C3C', 'marker_size': 8,
              'marker_outline': '#C0392B', 'marker_name': 'diamond'}),
        ]
        
        layers_to_add = []
        first_valid_crs = None
        
        for layer_name, description, profile_type, style in layer_configs:
            layer = TwwLayerManager.layer(layer_name)
            # Fallback for potential naming differences in DB/views.
            if not layer and layer_name == 'vw_change_points':
                layer = TwwLayerManager.layer('vm_change_points')
            if not layer:
                continue
            # Special handling for vw_tww_reach: create temp layer with Z values
            # because the original geometry doesn't have proper Z values
            if layer_name == 'vw_tww_reach':
                self._temp_reach_layer = self._createReachLayerWithZ(layer)
                if self._temp_reach_layer:
                    layer = self._temp_reach_layer
            
            # Store first valid CRS for canvas
            if first_valid_crs is None and layer.crs().isValid():
                first_valid_crs = layer.crs()
            
            # 3. Configure elevation properties for each layer
            # Set to "Absolute" - use Z values from geometry directly
            elevation_props = layer.elevationProperties()
            if elevation_props and isinstance(elevation_props, QgsVectorLayerElevationProperties):
                try:
                    # Enable elevation for this layer
                    if hasattr(elevation_props, 'setEnabled'):
                        elevation_props.setEnabled(True)
                    
                    # Set profile type: Continuous Surface or Individual Features
                    # vw_wastewater_node uses "Continuous Surface" (Durchgehende Oberfläche)
                    if hasattr(Qgis, 'VectorProfileType'):
                        if profile_type == 'surface':
                            elevation_props.setType(Qgis.VectorProfileType.ContinuousSurface)
                        else:
                            elevation_props.setType(Qgis.VectorProfileType.IndividualFeatures)
                    
                    # Set clamping to Absolute (use geometry Z values)
                    # This is the key setting that makes it work!
                    if hasattr(Qgis, 'AltitudeClamping'):
                        elevation_props.setClamping(Qgis.AltitudeClamping.Absolute)
                    elif hasattr(elevation_props, 'setClamping'):
                        elevation_props.setClamping(0)
                    
                    # Set binding to vertex (for line layers, use vertex Z values)
                    if hasattr(Qgis, 'AltitudeBinding'):
                        elevation_props.setBinding(Qgis.AltitudeBinding.Vertex)
                    
                    # 4. Configure profile symbols for better appearance
                    self._configureLayerSymbols(elevation_props, style, layer_name)
                    
                    # Force layer to recognize changes
                    layer.triggerRepaint()
                    
                except Exception:
                    pass
            else:
                pass
            
            layers_to_add.append(layer)
        
        if not layers_to_add:
            print("✗ No layers found! Available layers in project:")
            for layer_id, layer in project.mapLayers().items():
                print(f"    - {layer.name()}")
            print("=" * 60)
            return
        
        if first_valid_crs:
            self.canvas.setCrs(first_valid_crs)
        self.canvas.setLayers(layers_to_add)
        self.canvas.setTolerance(self._manhole_dash_tolerance)

    def _onJobCountChanged(self, count):
        """
        Called when profile generation jobs change.
        When count reaches 0, generation is complete and identify should work.
        """
        if count == 0 and not self._profile_generation_complete:
            self._profile_generation_complete = True
    
    def _setupHoverHandling(self):
        """
        Set up hover handling for the elevation profile canvas.
        
        Uses official QGIS APIs:
        - setSnappingEnabled: Enable cursor snapping to profile features
        - canvasPointHovered signal: Get hover events (may be unreliable)
        - mouseMoveEvent: Fallback mechanism for reliable hover detection
        
        Note: Mouse tracking is already enabled in TwwElevationProfileCanvas.__init__
        """
        # Enable official snapping feature (QGIS 3.26+)
        if hasattr(self.canvas, "setSnappingEnabled"):
            self.canvas.setSnappingEnabled(True)
        if hasattr(self.canvas, "canvasPointHovered"):
            self.canvas.canvasPointHovered.connect(self._onCanvasPointHovered)
    
    def _onCanvasPointHovered(self, _map_point, profile_point):
        """
        Handle hover signal from QgsElevationProfileCanvas (official API).
        
        This is the preferred method but may not trigger in all QGIS versions.
        """
        if not self._hover_enabled:
            return
        self._updateHoverMatch(profile_point)

    def _onCanvasMouseMove(self, event):
        if not self._hover_enabled:
            return
        self._last_hover_pos = event.pos()
        self._last_hover_global_pos = self.canvas.mapToGlobal(event.pos())
        # Always run fallback hover matching since the hover signal is unreliable
        # (unreliable means: may not trigger in all QGIS versions or situations)
        self._handleCanvasHover(event.pos())

    def _onCanvasLeave(self, _event):
        self._clearHoverState()

    def _handleCanvasHover(self, pos):
        """
        Handle hover using raw canvas coordinates.
        """
        if not hasattr(self.canvas, "canvasPointToPlotPoint"):
            return
        if hasattr(self.canvas, "plotArea"):
            try:
                plot_area = self.canvas.plotArea()
                if plot_area and not plot_area.contains(QPointF(pos)):
                    self._clearHoverState()
                    return
            except Exception:
                pass
        if hasattr(self.canvas, "snapToPlot"):
            try:
                self.canvas.snapToPlot(pos)
            except Exception:
                pass
        profile_point = self.canvas.canvasPointToPlotPoint(QPointF(pos))
        if self._isEmptyProfilePoint(profile_point):
            self._clearHoverState()
            return
        self._updateHoverMatch(profile_point)

    def _updateHoverMatch(self, profile_point):
        """
        Convert hover to plot coordinates and match the nearest profile result.
        
        ALTERNATIVE APPROACH: Direct layer querying instead of identify()
        
        The official identify() API doesn't work reliably in all QGIS versions,
        so we directly query layers and manually find the nearest feature.
        """
        plot_point = self._profilePointToPlotPoint(profile_point)
        if plot_point is None:
            self._clearHoverState()
            return

        # Try official identify() API first
        # IMPORTANT: identify() expects CANVAS coordinates (pixel position), NOT plot coordinates!
        identify_results = []
        if hasattr(self.canvas, "identify") and self._last_hover_pos is not None:
            try:
                # Use canvas coordinates (mouse position), not plot coordinates
                canvas_point = QPointF(self._last_hover_pos)
                identify_results = self.canvas.identify(canvas_point)
            except Exception:
                pass
        
        # Check for custom-drawn manhole dashes (not in layers, drawn via drawForeground)
        manhole_match = self._identifyManholeDash(plot_point)
        if manhole_match:
            identify_results.append(manhole_match)
        
        # Find the nearest match to the cursor position
        nearest = self._nearestIdentifyResult(identify_results, plot_point)
        
        # IMPORTANT: Keep the last match if no new match is found
        # This prevents tooltip from disappearing when mouse moves slightly
        if nearest:
            self._last_hover_match = nearest
        
        # Show tooltip with current or last match
        self._showHoverTooltip(plot_point, self._last_hover_match)

        # Highlight the hovered feature on the main map canvas
        if self._last_hover_match:
            self._highlightMatchOnMap(plot_point, self._last_hover_match)
        else:
            self._clearHighlight()

    def _identifyManholeDash(self, plot_point):
        """
        Identify custom-drawn manhole dashes (drawn via drawForeground, not in layers).
        
        Manhole dashes are stored in self.canvas._manhole_dashes and are not
        identifiable via the official identify() API.
        
        :param plot_point: QPointF with (distance, elevation) in plot coordinates
        :return: A fake identify result dict, or None if no match
        """
        if not hasattr(self.canvas, '_manhole_dashes') or not self.canvas._manhole_dashes:
            return None
        
        distance = plot_point.x()
        elevation = plot_point.y()
        
        # Tolerance for matching (in plot coordinates)
        tolerance_dist = 5.0  # meters along profile
        tolerance_elev = 5.0  # meters vertical (generous for vertical shafts)
        
        best_match = None
        best_distance2 = float('inf')
        
        for dash in self.canvas._manhole_dashes:
            dash_distance = dash.get("distance")
            cover_level = dash.get("cover_level")
            bottom_level = dash.get("bottom_level")
            width_px = dash.get("width", 10)
            
            if dash_distance is None or cover_level is None or bottom_level is None:
                continue
            
            # Check horizontal distance (along profile)
            dist_diff = abs(distance - dash_distance)
            if dist_diff > tolerance_dist:
                continue
            
            # Check if elevation is within manhole shaft (bottom to cover)
            min_elev = min(bottom_level, cover_level) - tolerance_elev
            max_elev = max(bottom_level, cover_level) + tolerance_elev
            
            if not (min_elev <= elevation <= max_elev):
                continue
            
            # Calculate distance squared for sorting (prefer center of shaft)
            center_elev = (cover_level + bottom_level) / 2
            elev_diff = abs(elevation - center_elev)
            distance2 = dist_diff * dist_diff + elev_diff * elev_diff
            
            if distance2 < best_distance2:
                best_distance2 = distance2
                # Create fake result compatible with _nearestIdentifyResult
                best_match = {
                    "layer": None,  # No layer for custom-drawn manholes
                    "result": {
                        "feature": None,
                        "attributes": {
                            "obj_id": dash.get("obj_id"),
                            "cover_level": cover_level,
                            "bottom_level": bottom_level,
                            "width": width_px,
                            "node_type": "manhole",  # Mark as manhole for tooltip formatting
                            "_is_manhole_dash": True,  # Special flag
                        },
                        "distance": dash_distance,
                        "elevation": center_elev,
                    },
                    "plot_point": QPointF(dash_distance, center_elev),
                    "distance2": distance2,
                }
        
        return best_match
        
    def _nearestIdentifyResult(self, identify_results, plot_point):
        """
        Pick the nearest identify result to a plot point.
        
        Handles both official QgsProfileIdentifyResults and our fallback fake results.
        """
        if not identify_results:
            return None

        best_match = None
        fallback_match = None
        
        for identify_result in identify_results:
            # Check if this is a fake result (dict) or official result (QgsProfileIdentifyResults)
            if isinstance(identify_result, dict):
                # This is our fallback fake result, already in the right format
                if best_match is None or identify_result["distance2"] < best_match.get("distance2", float('inf')):
                    best_match = identify_result
                continue
            
            # Official identify result - process as before
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
                if best_match is None or (best_match["distance2"] is not None and distance2 < best_match["distance2"]):
                    best_match = {
                        "layer": layer,
                        "result": result,
                        "plot_point": candidate_point,
                        "distance2": distance2,
                    }
        return best_match or fallback_match

    def _extractPlotPointFromResult(self, result):
        """
        Try to extract a plot point (distance/elevation) from a QVariantMap result.
        """
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
        """
        Convert QgsProfilePoint to QPointF (distance, elevation).
        """
        if profile_point is None:
            return None
        distance = None
        elevation = None
        if hasattr(profile_point, "distance"):
            distance = profile_point.distance() if callable(profile_point.distance) else profile_point.distance
        if hasattr(profile_point, "elevation"):
            elevation = profile_point.elevation() if callable(profile_point.elevation) else profile_point.elevation
        if distance is None or elevation is None:
            return None
        return QPointF(float(distance), float(elevation))

    def _isEmptyProfilePoint(self, profile_point):
        if profile_point is None:
            return True
        if hasattr(profile_point, "isEmpty"):
            return profile_point.isEmpty()
        return False

    def _clearHoverState(self):
        self._last_hover_match = None
        self._last_tooltip_text = None
        # Hide both QToolTip and custom tooltip
        QToolTip.hideText()
        if hasattr(self, '_custom_tooltip'):
            self._custom_tooltip.hide()
        self._clearHighlight()

    def _showHoverTooltip(self, plot_point, match):
        # Check if we should hide the tooltip
        should_hide = (
            plot_point is None
            or match is None
        )
        
        if should_hide:
            QToolTip.hideText()
            if hasattr(self, '_custom_tooltip'):
                self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return
        
        text = self._formatHoverSummary(plot_point, match)
        if not text:
            QToolTip.hideText()
            if hasattr(self, '_custom_tooltip'):
                self._custom_tooltip.hide()
            self._last_hover_global_pos = None
            self._last_tooltip_text = None
            return
        
        # Check if this is the same feature and same text as last time
        current_layer = match.get("layer_name", "")
        current_fid = match.get("feature_id", "")
        same_feature = False
        
        if self._last_hover_match:
            last_layer = self._last_hover_match.get("layer_name", "")
            last_fid = self._last_hover_match.get("feature_id", "")
            same_feature = (current_layer == last_layer and current_fid == last_fid)
        
        # Get current mouse position
        current_pos = QCursor.pos()
        
        # Only call showText() when necessary to avoid Qt tooltip issues
        # Do NOT call showText() on every mouse move!
        need_update = False
        
        # Case 1: No previous tooltip or feature changed
        if not hasattr(self, '_last_tooltip_text') or not self._last_tooltip_text:
            need_update = True
        elif not same_feature:
            need_update = True
        # Case 2: Text content changed (data updated)
        elif text != self._last_tooltip_text:
            need_update = True
        # Case 3: Mouse moved significantly (optional, for tooltip position update)
        elif self._last_hover_global_pos:
            dx = abs(current_pos.x() - self._last_hover_global_pos.x())
            dy = abs(current_pos.y() - self._last_hover_global_pos.y())
            if dx > 50 or dy > 50:  # Only update position if moved > 50 pixels
                need_update = True
        
        # Only update tooltip when needed
        if need_update:
            self._last_hover_global_pos = current_pos
            self._last_tooltip_text = text
            # Use custom persistent tooltip instead of QToolTip
            # This avoids Qt's automatic timeout behavior
            if hasattr(self, '_custom_tooltip'):
                self._custom_tooltip.setText(text)
                self._custom_tooltip.adjustSize()
                # Position tooltip slightly offset from cursor
                tooltip_pos = QPoint(current_pos.x() + 10, current_pos.y() + 10)
                self._custom_tooltip.move(tooltip_pos)
                self._custom_tooltip.show()
                self._custom_tooltip.raise_()
            else:
                QToolTip.showText(current_pos, text, self.canvas, QRect(), 120000)

    def _formatHoverSummary(self, plot_point, match):
        """
        Build tooltip text for hover matches.
        """
        result = match.get("result") if match else None
        layer = match.get("layer") if match else None
        layer_name = layer.name() if layer and hasattr(layer, "name") else ""
        # Extract feature first, as it contains the complete attributes
        feature = self._extractResultFeature(result, layer)
        result_attrs = self._extractResultAttributes(result)

        # Priority 1: Get attributes from feature (contains full business attributes)
        if feature:
            attrs = self._featureAttributes(feature)
        else:
            # Priority 2: Fallback to result attributes (only profile attributes)
            attrs = result_attrs

        # Merge profile-specific attributes from result (distance, elevation, etc.)
        if result_attrs:
            for key in ['distance', 'elevation', 'delta']:
                if key in result_attrs and key not in attrs:
                    attrs[key] = result_attrs[key]

        lines = []
        
        # Debug: check which branch we enter
        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name, attrs)
        is_manhole = self._isManholeHover(layer_name, attrs)
        if is_reach:
            obj_id = self._pickAttr(attrs, ["obj_id", "objId", "id", "reach_id"])
            title = f"Reach {obj_id}" if obj_id else "Reach"
            lines.append(title)
            # Try material abbreviation first (more readable), then fall back to material code
            material = self._pickAttr(attrs, ["material_abbr_en", "material_abbr_de", "material_abbr_fr", "material"])
            self._appendLabeled(lines, "Material", material)
            width_mm = self._toFloat(self._pickAttr(attrs, ["clear_height", "width", "diameter"]))
            if width_mm is not None:
                lines.append(f"Width: {width_mm:.0f} mm")
            # Try actual database field names for vw_tww_reach
            length = self._toFloat(self._pickAttr(attrs, ["ch_pipe_length", "length_effective", "length_full", "length"]))
            if length is None:
                length = self._lengthFromFeature(feature)
            self._appendLabeled(lines, "Length", self._formatMeters(length))
            gradient = self._deriveGradient(attrs, length)
            if gradient is not None:
                lines.append(f"Gradient: {gradient * 1000:.0f} ‰")
            # Try actual database field names: rp_from_level and rp_to_level
            entry_level = self._toFloat(self._pickAttr(attrs, ["rp_from_level", "from_level", "start_level", "startLevel"]))
            exit_level = self._toFloat(self._pickAttr(attrs, ["rp_to_level", "to_level", "end_level", "endLevel"]))
            if entry_level is None or exit_level is None:
                start_z, end_z = self._levelsFromFeatureGeometry(feature)
                if entry_level is None:
                    entry_level = start_z
                if exit_level is None:
                    exit_level = end_z
            self._appendLabeled(lines, "Entry level", self._formatMeters(entry_level, decimals=1))
            self._appendLabeled(lines, "Exit level", self._formatMeters(exit_level, decimals=1))
            # Show elevation at cursor position (interpolated along reach)
            if plot_point is not None and entry_level is not None and exit_level is not None:
                cursor_elev = plot_point.y()
                lines.append(f"Elevation at cursor: {cursor_elev:.2f} m")
        elif is_cover:
            # Cover tooltip format
            obj_id = self._pickAttr(attrs, ["obj_id", "objId", "id"])
            title = f"Cover {obj_id}" if obj_id else "Cover"
            lines.append(title)
            
            # Get enhanced cover data from vm_cover layer
            cover_data = self._getCoverEnhancedData(obj_id, attrs)
            
            # Level
            level = cover_data.get("level") or self._toFloat(self._pickAttr(attrs, ["level", "cover_level"]))
            self._appendLabeled(lines, "Level", self._formatMeters(level, decimals=2))
            
            # Material
            material = cover_data.get("material") or self._pickAttr(attrs, ["material"])
            self._appendLabeled(lines, "Material", material)
            
            # Cover shape
            cover_shape = cover_data.get("cover_shape") or self._pickAttr(attrs, ["cover_shape"])
            self._appendLabeled(lines, "Cover shape", cover_shape)
            
            # Brand
            brand = cover_data.get("brand") or self._pickAttr(attrs, ["brand"])
            self._appendLabeled(lines, "Brand", brand)
        elif is_manhole:
            node_type = str(self._pickAttr(attrs, ["node_type", "nodeType", "type"]) or "").lower()
            is_actual_manhole = "manhole" in node_type
            obj_id = self._pickAttr(attrs, ["obj_id", "objId", "id", "ws_obj_id"])
            
            if is_actual_manhole:
                # Full manhole display with all details
                title = f"Manhole: {obj_id}" if obj_id else "Manhole"
                lines.append(title)
                # Get enhanced manhole data from related tables
                manhole_data = self._getManholeEnhancedData(obj_id, attrs, feature)
                
                # Cover level (from vm_cover.level or vw_cover.level)
                cover_level = manhole_data.get("cover_level")
                self._appendLabeled(lines, "Cover level", self._formatMeters(cover_level, decimals=2))
                
                # Bottom level (from vm_wastewater_node.botoom_level)
                bottom_level = manhole_data.get("bottom_level")
                self._appendLabeled(lines, "Bottom level", self._formatMeters(bottom_level, decimals=2))
                
                # Entry level (from upstream reach's exit level)
                entry_level = manhole_data.get("entry_level")
                self._appendLabeled(lines, "Entry level", self._formatMeters(entry_level, decimals=2))
                
                # Exit level (from downstream reach's entry level)
                exit_level = manhole_data.get("exit_level")
                self._appendLabeled(lines, "Exit level", self._formatMeters(exit_level, decimals=2))
                
                # Depth
                if cover_level is not None and bottom_level is not None:
                    depth = cover_level - bottom_level
                    self._appendLabeled(lines, "Depth", self._formatMeters(depth, decimals=2))
                
                # Width (if available)
                width = manhole_data.get("width")
                if width is not None:
                    lines.append(f"Width: {width:.0f} mm")
            else:
                # Simple node display (not a manhole)
                title = f"Node: {obj_id}" if obj_id else "Node"
                lines.append(title)
                
                # Show node type if available
                if node_type:
                    lines.append(f"Type: {node_type}")
                
                # Show bottom level (elevation)
                bottom_level = self._toFloat(self._pickAttr(attrs, ["bottom_level", "bottomLevel", "level", "invert_level"]))
                if bottom_level is not None:
                    lines.append(f"Level: {bottom_level:.2f} m")
                elif plot_point is not None:
                    lines.append(f"Elevation: {plot_point.y():.2f} m")
        else:
            if layer_name:
                lines.append(layer_name)

        # Only show distance/elevation for unknown feature types (not reach, cover, or manhole)
        if plot_point is not None and not is_reach and not is_cover and not is_manhole:
            lines.append(f"distance: {plot_point.x():.2f}")
            lines.append(f"elevation: {plot_point.y():.2f}")
        return "\n".join(lines)

    def _extractResultAttributes(self, result):
        """
        Extract attribute dict from a QgsElevationProfile identify result.
        """
        if not result:
            return {}
        if isinstance(result, dict):
            if "attributes" in result and isinstance(result["attributes"], dict):
                return dict(result["attributes"])
            if "feature" in result:
                feature = result.get("feature")
                attrs = self._featureAttributes(feature)
                if attrs:
                    return attrs
            return {k: v for k, v in result.items() if not isinstance(v, (dict, list))}
        return {}

    def _extractResultFeature(self, result, layer):
        """
        Try to resolve the QgsFeature from an identify result.
        """
        if not result:
            return None
        if isinstance(result, dict):
            feature = result.get("feature")
            if feature is not None:
                return feature
            fid = result.get("featureId")
            if fid is None:
                fid = result.get("fid") or result.get("id")
            if fid is not None and layer is not None and hasattr(layer, "getFeature"):
                try:
                    return layer.getFeature(int(fid))
                except Exception:
                    try:
                        return layer.getFeature(fid)
                    except Exception:
                        return None
        return None

    def _featureAttributes(self, feature):
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
            start_z = self._pointZ(points[0])
            end_z = self._pointZ(points[-1])
            return (start_z, end_z)
        except Exception:
            return (None, None)

    def _pointZ(self, point):
        if point is None:
            return None
        if hasattr(point, "z"):
            try:
                z_value = point.z() if callable(point.z) else point.z
                return self._toFloat(z_value)
            except Exception:
                return None
        return None

    def _pickAttr(self, attrs, keys):
        if not attrs:
            return None
        for key in keys:
            if key in attrs:
                return attrs.get(key)
        lower_map = {str(k).lower(): v for k, v in attrs.items()}
        for key in keys:
            key_lower = key.lower()
            if key_lower in lower_map:
                return lower_map.get(key_lower)
        return None

    def _toFloat(self, value):
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

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

    def _deriveGradient(self, attrs, length):
        # Try gradient fields first (may be pre-calculated)
        gradient = self._toFloat(self._pickAttr(attrs, ["_slope_per_mill", "gradient", "slope"]))
        if gradient is not None:
            # _slope_per_mill is already in per-mille, gradient/slope might be ratio
            # If value is between 0 and 1, it's likely a ratio, needs *1000
            if abs(gradient) < 1:
                return gradient  # Already in per-mille or very small
            # If > 1, assume it's already per-mille
            return gradient / 1000  # Convert to ratio for display
        if length is None or length == 0:
            return None
        # Try actual database field names for vw_tww_reach
        from_level = self._toFloat(self._pickAttr(attrs, ["rp_from_level", "from_level", "start_level", "startLevel"]))
        to_level = self._toFloat(self._pickAttr(attrs, ["rp_to_level", "to_level", "end_level", "endLevel"]))
        if from_level is None or to_level is None:
            return None
        return (from_level - to_level) / float(length)

    def _isReachHover(self, layer_name, attrs):
        """Check if hover is over a reach/pipe layer"""
        layer_name_lower = (layer_name or "").lower()
        
        # If layer name explicitly contains reach, return True
        if "reach" in layer_name_lower:
            return True
        
        # If layer name explicitly indicates other types, return False
        if "wastewater_node" in layer_name_lower or "manhole" in layer_name_lower:
            return False
        if "cover" in layer_name_lower:
            return False
        if "change_point" in layer_name_lower:
            return False
        
        # Only use attribute check when layer name is ambiguous
        # Require multiple reach-specific attributes to avoid false positives
        has_material = self._pickAttr(attrs, ["material"]) is not None
        has_length = self._pickAttr(attrs, ["length_full", "length"]) is not None
        return has_material and has_length

    def _getCoverEnhancedData(self, obj_id, attrs):
        """
        Get enhanced cover data from vm_cover layer.
        
        Fields in vm_cover:
        - obj_id
        - level
        - material (code value)
        - cover_shape (code value)
        - brand
        """
        result = {}
        
        if not obj_id:
            return result
        
        # Try vm_cover first, then vw_cover
        cover_layer = TwwLayerManager.layer("vm_cover")
        if cover_layer is None:
            cover_layer = TwwLayerManager.layer("vw_cover")
        
        if cover_layer is None:
            # Fallback to current attrs
            result["level"] = self._toFloat(self._pickAttr(attrs, ["level"]))
            result["material"] = self._pickAttr(attrs, ["material"])
            result["cover_shape"] = self._pickAttr(attrs, ["cover_shape"])
            result["brand"] = self._pickAttr(attrs, ["brand"])
            return result
        
        # Query vm_cover by obj_id
        for feat in cover_layer.getFeatures():
            feat_attrs = self._featureAttributes(feat)
            feat_obj_id = self._pickAttr(feat_attrs, ["obj_id", "objId", "id"])
            
            if str(feat_obj_id) == str(obj_id):
                # Found matching cover
                result["level"] = self._toFloat(self._pickAttr(feat_attrs, ["level"]))
                result["material"] = self._pickAttr(feat_attrs, ["material"])
                result["cover_shape"] = self._pickAttr(feat_attrs, ["cover_shape"])
                result["brand"] = self._pickAttr(feat_attrs, ["brand"])
                break
        
        return result

    def _getManholeEnhancedData(self, obj_id, attrs, feature):
        """
        Get enhanced manhole data from related tables:
        - Cover level from vm_cover.level
        - Bottom level from vm_wastewater_node.botoom_level (note typo)
        - Entry level from tww_wastewater_structure._input_label
        - Exit level from tww_wastewater_structure._output_label
        - Width (if available)
        """
        result = {}
        # Get wastewater structure ID for querying related tables
        ws_id = self._pickAttr(attrs, [
            "fk_wastewater_structure",
            "fk_wastewater_structure_obj_id",
            "ws_obj_id",
            "fk_wastewater_structure_id",
            "obj_id"
        ])
        if not ws_id:
            ws_id = obj_id
        # Query tww_wastewater_structure for _input_label and _output_label
        # These fields already contain the correct entry/exit levels
        ws_layer = TwwLayerManager.layer("vw_tww_wastewater_structure")
        if ws_layer is None:
            ws_layer = TwwLayerManager.layer("tww_wastewater_structure")
        if ws_layer and ws_id:
            found_ws = False
            for ws_feat in ws_layer.getFeatures():
                ws_attrs = self._featureAttributes(ws_feat)
                ws_obj_id = self._pickAttr(ws_attrs, ["obj_id", "objId", "id"])
                if str(ws_obj_id) == str(ws_id):
                    # Found matching wastewater structure
                    # Get entry/exit levels from _input_label and _output_label
                    result["entry_level"] = self._toFloat(
                        self._pickAttr(ws_attrs, ["_input_label", "input_label", "entry_level"])
                    )
                    result["exit_level"] = self._toFloat(
                        self._pickAttr(ws_attrs, ["_output_label", "output_label", "exit_level"])
                    )
                    found_ws = True
                    break
        
        # 1. Query cover level from vm_cover or vw_cover
        cover_layer = TwwLayerManager.layer("vm_cover")
        if cover_layer is None:
            cover_layer = TwwLayerManager.layer("vw_cover")
        if cover_layer and ws_id:
            found_cover = False
            for cover_feat in cover_layer.getFeatures():
                cover_attrs = self._featureAttributes(cover_feat)
                cover_ws_id = self._pickAttr(cover_attrs, [
                    "fk_wastewater_structure",
                    "fk_wastewater_structure_obj_id", 
                    "ws_obj_id",
                    "fk_wastewater_structure_id"
                ])
                if str(cover_ws_id) == str(ws_id):
                    # Found matching cover - get level
                    result["cover_level"] = self._toFloat(
                        self._pickAttr(cover_attrs, ["level", "cover_level", "coverLevel", "elevation"])
                    )
                    found_cover = True
                    break
        
        # Fallback: try to get cover_level from current attrs
        if "cover_level" not in result or result["cover_level"] is None:
            result["cover_level"] = self._toFloat(
                self._pickAttr(attrs, ["cover_level", "coverLevel"])
            )
        # 2. Query bottom level from vm_wastewater_node or vw_wastewater_node
        # Note: field name is "botoom_level" (typo in DB)
        node_layer = TwwLayerManager.layer("vw_wastewater_node")
        if node_layer and obj_id:
            found_node = False
            for node_feat in node_layer.getFeatures():
                node_attrs = self._featureAttributes(node_feat)
                node_obj_id = self._pickAttr(node_attrs, ["obj_id", "objId", "id"])
                if str(node_obj_id) == str(obj_id):
                    # Found matching node - get bottom level
                    result["bottom_level"] = self._toFloat(
                        self._pickAttr(node_attrs, ["botoom_level", "bottom_level", "bottomLevel", "invert_level"])
                    )
                    # Also try to get width from node
                    result["width"] = self._toFloat(
                        self._pickAttr(node_attrs, ["dimension1", "width", "diameter"])
                    )
                    found_node = True
                    break
        
        # Fallback: try to get bottom_level from current attrs
        if "bottom_level" not in result or result["bottom_level"] is None:
            result["bottom_level"] = self._toFloat(
                self._pickAttr(attrs, ["botoom_level", "bottom_level", "bottomLevel", "invert_level"])
            )
        return result
    
    def _isManholeHover(self, layer_name, attrs):
        """Check if hover is over a manhole/wastewater_node layer or custom-drawn manhole dash"""
        # Check for custom-drawn manhole dash (from _identifyManholeDash)
        if attrs and attrs.get("_is_manhole_dash"):
            return True
        
        layer_name_lower = (layer_name or "").lower()
        
        # If layer name explicitly contains wastewater_node or manhole, return True
        if "wastewater_node" in layer_name_lower or "manhole" in layer_name_lower:
            return True
        
        # If layer name explicitly indicates other types, return False
        if "reach" in layer_name_lower:
            return False
        if "cover" in layer_name_lower and "wastewater" not in layer_name_lower:
            return False
        if "change_point" in layer_name_lower:
            return False
        
        # Check node_type attribute
        node_type = str(self._pickAttr(attrs, ["node_type", "nodeType", "type"]) or "").lower()
        if "manhole" in node_type:
            return True
        
        # Only use attribute check when layer name is ambiguous
        # Require both cover_level and bottom_level (manhole-specific) to avoid false positives
        has_cover = self._pickAttr(attrs, ["cover_level"]) is not None
        has_bottom = self._pickAttr(attrs, ["bottom_level"]) is not None
        return has_cover and has_bottom

    def _isCoverHover(self, layer_name, attrs):
        """Check if hover is over a cover layer"""
        layer_name_lower = (layer_name or "").lower()
        
        # If layer name explicitly contains cover (but not wastewater_node), return True
        if "cover" in layer_name_lower and "wastewater_node" not in layer_name_lower:
            return True
        
        return False

    def _configureLayerSymbols(self, elevation_props, style, layer_name):
        """
        Configure profile symbols for a layer to improve visual appearance.
        
        :param elevation_props: QgsVectorLayerElevationProperties
        :param style: dict with 'line', 'line_width', 'fill', 'marker', 'marker_size'
                      Optional 'hollow': True to create hollow/outline effect for lines
        :param layer_name: Name of the layer for logging
        """
        try:
            # Create and set line symbol
            if hasattr(elevation_props, 'setProfileLineSymbol'):
                # Check if hollow/outline effect is requested
                if style.get('hollow', False):
                    # Create hollow line effect: outer line (dark) + inner line (light)
                    line_symbol = QgsLineSymbol()
                    line_symbol.deleteSymbolLayer(0)  # Remove default layer
                    
                    # Layer 1: Inner fill (lighter color, wider)
                    inner_width = style['line_width']
                    inner_layer = QgsSimpleLineSymbolLayer()
                    inner_layer.setColor(QColor(style.get('line_inner', '#FFFFFF')))
                    inner_layer.setWidth(inner_width)
                    inner_layer.setPenCapStyle(Qt.FlatCap)
                    inner_layer.setPenJoinStyle(Qt.RoundJoin)
                    line_symbol.appendSymbolLayer(inner_layer)
                    
                    # Layer 2: Top outline (darker, thinner)
                    outline_width = style.get('outline_width', 0.8)
                    outline_layer = QgsSimpleLineSymbolLayer()
                    outline_layer.setColor(QColor(style['line']))
                    outline_layer.setWidth(outline_width)
                    outline_layer.setOffset(inner_width / 2 - outline_width / 2)
                    outline_layer.setPenCapStyle(Qt.FlatCap)
                    outline_layer.setPenJoinStyle(Qt.RoundJoin)
                    line_symbol.appendSymbolLayer(outline_layer)
                    
                    # Layer 3: Bottom outline (darker, thinner)
                    bottom_layer = QgsSimpleLineSymbolLayer()
                    bottom_layer.setColor(QColor(style['line']))
                    bottom_layer.setWidth(outline_width)
                    bottom_layer.setOffset(-(inner_width / 2 - outline_width / 2))
                    bottom_layer.setPenCapStyle(Qt.FlatCap)
                    bottom_layer.setPenJoinStyle(Qt.RoundJoin)
                    line_symbol.appendSymbolLayer(bottom_layer)
                else:
                    # Standard solid line
                    line_symbol = QgsLineSymbol.createSimple({
                        'color': style['line'],
                        'width': str(style['line_width']),
                        'capstyle': 'round',
                        'joinstyle': 'round'
                    })
                elevation_props.setProfileLineSymbol(line_symbol)
            
            # Create and set fill symbol (for areas under the profile line)
            if hasattr(elevation_props, 'setProfileFillSymbol'):
                fill_symbol = QgsFillSymbol.createSimple({
                    'color': style['fill'],
                    'outline_color': style['line'],
                    'outline_width': '0.5'
                })
                elevation_props.setProfileFillSymbol(fill_symbol)
            
            # Create and set marker symbol (for points/vertices)
            if hasattr(elevation_props, 'setProfileMarkerSymbol'):
                marker_symbol = QgsMarkerSymbol.createSimple({
                    'color': style['marker'],
                    'size': str(style['marker_size']),
                    'outline_color': style.get('marker_outline', '#FFFFFF'),
                    'outline_width': '0.5',
                    'name': style.get('marker_name', 'circle')
                })
                elevation_props.setProfileMarkerSymbol(marker_symbol)
                # Ensure markers are actually drawn if the API supports it.
                if hasattr(elevation_props, 'setShowMarkers'):
                    elevation_props.setShowMarkers(True)
                if hasattr(elevation_props, 'setShowMarker'):
                    elevation_props.setShowMarker(True)
                if hasattr(elevation_props, 'setShowPoints'):
                    elevation_props.setShowPoints(True)
            
            # Optionally respect layer symbology (use original layer colors)
            # Set to False to use our custom profile symbols instead
            if hasattr(elevation_props, 'setRespectLayerSymbology'):
                elevation_props.setRespectLayerSymbology(False)
        except Exception:
            pass

    def setProfileCurve(self, geometry):
        """
        Set the profile curve (path) for the elevation profile.
        
        :param geometry: QgsGeometry object representing the path
        """
        if not isinstance(geometry, QgsGeometry) or geometry.isEmpty():
            return
        points = geometry.asPolyline()
        if not points:
            return
        
        # Create a new QgsLineString with the points
        curve = QgsLineString(points)
        
        # Ensure data sources are set up before setting the curve
        # This is critical because the canvas needs layers and project to display the profile
        layers_configured = False
        if hasattr(self.canvas, 'layers'):
            layers = self.canvas.layers()
            layers_configured = layers and len(layers) > 0
        
        if not layers_configured or not getattr(self, '_data_sources_setup', False):
            self.setupDataSources()
            self._data_sources_setup = True
        
        # Cancel any running jobs before setting new curve
        if hasattr(self.canvas, 'cancelJobs'):
            self.canvas.cancelJobs()
        if hasattr(self.canvas, 'invalidateCurrentPlotExtent'):
            self.canvas.invalidateCurrentPlotExtent()
        self.canvas.setProfileCurve(curve)
        self.canvas.refresh()

        # Cache profile geometry for manhole dashes rendering
        self._profile_curve_geom = geometry
        self._refreshManholeDashes()
        
        # Use QTimer to delay zoomFull - gives the canvas time to process
        def delayedZoomFull():
            if hasattr(self.canvas, 'zoomFull'):
                try:
                    self.canvas.zoomFull()
                except Exception:
                    pass
        
        # Delay zoomFull by 100ms to allow canvas to process
        QTimer.singleShot(100, delayedZoomFull)
    

    def setProfileFromTree(self, nodes, edges):
        """
        Set the profile curve from tree data (nodes and edges).
        
        This method builds a polyline geometry from edges (reaches) and sets it as the profile curve.
        It works similar to how onSelectCurrentPathAction selects features - it uses the same data structure.
        
        :param nodes: List of nodes from tree map tool (not directly used, but kept for compatibility)
        :param edges: List of edges from tree map tool, each edge is a tuple (from_node, to_node, edge_info)
        """
        # Get the reach layer (same as used in onSelectCurrentPathAction)
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer or not edges:
            return
        
        # Collect reach IDs from edges
        # Edge structure: (from_node, to_node, edge_info_dict)
        # edge_info contains: {"objType": "reach", "baseFeature": obj_id, ...}
        reach_ids = []
        for item in edges:
            item_info = item[2]  # Get the edge info dictionary
            if item_info.get("objType") == "reach":
                base_feature = item_info.get("baseFeature")
                if base_feature:
                    reach_ids.append(base_feature)
        
        if not reach_ids:
            return
        
        # Build filter expression (same approach as onSelectCurrentPathAction)
        reach_list = ",".join("'" + id + "'" for id in reach_ids if id)
        request = QgsFeatureRequest()
        request.setFilterExpression(f"obj_id IN ({reach_list})")
        
        # Build polyline from reach geometries
        # We need to connect the geometries in the correct order
        points = []
        for feature in reach_layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry:
                polyline = geometry.asPolyline()
                if points:
                    # Connect to previous polyline (remove duplicate point if same)
                    if points[-1] == polyline[0]:
                        points.extend(polyline[1:])
                    else:
                        points.extend(polyline)
                else:
                    points.extend(polyline)
        
        if points:
            profile_geometry = QgsGeometry.fromPolylineXY(points)
            # setProfileCurve will call setupDataSources if needed
            self.setProfileCurve(profile_geometry)

    def _refreshManholeDashes(self):
        """
        Build vertical dashed lines (cover -> bottom) for manholes along the profile curve.
        """
        if self._profile_curve_geom is None or self._profile_curve_geom.isEmpty():
            self.canvas.setManholeDashes([])
            return

        layer = TwwLayerManager.layer("vw_wastewater_node")
        if layer is None:
            self.canvas.setManholeDashes([])
            return
        cover_layer = TwwLayerManager.layer("vw_cover")
        if cover_layer is None:
            cover_layer = TwwLayerManager.layer("vm_cover")

        cover_by_ws = {}
        if cover_layer is not None:
            for cover_feature in cover_layer.getFeatures():
                cover_attrs = self._featureAttributes(cover_feature)
                ws_id = self._pickAttr(
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
                cover_level = self._toFloat(self._pickAttr(cover_attrs, ["cover_level", "level", "elevation", "z"]))
                if cover_level is None:
                    cover_geom = cover_feature.geometry()
                    if cover_geom and not cover_geom.isEmpty():
                        cover_level = self._pointZ(cover_geom.asPoint())
                if cover_level is None:
                    continue
                cover_geom = cover_feature.geometry()
                cover_by_ws[str(ws_id)] = {
                    "cover_level": cover_level,
                    "geometry": cover_geom,
                }

        dashes = []
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            attrs = self._featureAttributes(feature)
            ws_id = self._pickAttr(
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
                cover_level = self._toFloat(self._pickAttr(attrs, ["cover_level", "coverLevel"]))
            if cover_level is None and cover_geometry is not None and not cover_geometry.isEmpty():
                cover_level = self._pointZ(cover_geometry.asPoint())
            bottom_level = self._toFloat(
                self._pickAttr(
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
            if bottom_level is None:
                bottom_level = self._pointZ(geometry.asPoint())
            if cover_level is None or bottom_level is None:
                continue

            line_width = self._manholeDashWidth(attrs)
            obj_id = self._pickAttr(attrs, ["obj_id", "objId", "id"])
            dashes.append(
                {
                    "distance": float(distance_along),
                    "cover_level": cover_level,
                    "bottom_level": bottom_level,
                    "width": line_width,
                    "obj_id": obj_id,  # Save obj_id for hover tooltip
                    "color": "#6E4C1E",  # Brown color for manhole shafts
                }
            )

        self.canvas.setManholeDashes(dashes)

    def _manholeDashWidth(self, attrs):
        """
        Calculate the line width for a manhole shaft based on its diameter.
        
        The width represents the visual thickness of the manhole shaft in the profile.
        Default is 1000mm (1m) if no diameter is found in attributes.
        
        TODO: Get actual diameter from database fields when available:
        - dimension1, dimension_1 (manhole dimensions)
        - diameter, manhole_diameter
        - clear_height (for special structures)
        
        :param attrs: Feature attributes dict (already extracted)
        :return: Line width in pixels (scaled from diameter)
        """
        diameter = self._toFloat(
            self._pickAttr(
                attrs,
                [
                    "dimension1",        # Primary manhole dimension
                    "dimension_1",       # Alternative naming
                    "diameter",          # Generic diameter
                    "manhole_diameter",  # Explicit manhole diameter
                    "diameter_mm",       # Diameter in mm
                    "width",             # Width field
                    "clear_height",      # For special structures
                ],
            )
        )
        
        # Use default pixel width if no diameter found
        default_px = self.canvas._manhole_default_px_width if hasattr(self.canvas, '_manhole_default_px_width') else 10
        
        if diameter is None:
            return default_px
        
        # Convert to mm if value appears to be in meters (< 10)
        diameter_mm = diameter * 1000.0 if diameter < 10 else diameter
        
        # Scale to pixel width: 1000mm -> 10px, range: 6px to 16px
        width = diameter_mm / 100.0
        return max(6.0, min(16.0, float(width)))

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

        attrs = self._featureAttributes(feature) if feature else result_attrs
        if result_attrs:
            for key in result_attrs:
                if key not in attrs:
                    attrs[key] = result_attrs[key]

        is_reach = self._isReachHover(layer_name, attrs)
        is_cover = self._isCoverHover(layer_name, attrs)
        is_manhole = self._isManholeHover(layer_name, attrs)
        obj_id = self._pickAttr(attrs, ["obj_id", "objId", "id"])

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
            # Find associated cover via fk_wastewater_structure
            ws_id = self._pickAttr(attrs, [
                "fk_wastewater_structure", "fk_wastewater_structure_obj_id", "ws_obj_id"
            ])
            if not ws_id and obj_id:
                # Look up node to get fk_wastewater_structure
                node_layer = TwwLayerManager.layer("vw_wastewater_node")
                if node_layer:
                    req = QgsFeatureRequest().setFilterExpression(
                        f'"obj_id" = \'{obj_id}\''
                    )
                    req.setLimit(1)
                    for nf in node_layer.getFeatures(req):
                        na = self._featureAttributes(nf)
                        ws_id = self._pickAttr(na, [
                            "fk_wastewater_structure", "ws_obj_id"
                        ])
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

        :param layer_name: Name of the layer to query
        :param filter_expr: QgsFeatureRequest filter expression
        :param highlight_key: Unique key to prevent duplicate highlights
        :param fallback_layer: Fallback layer name if primary is not found
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
            # Must remove from canvas scene — del only drops the Python reference,
            # but the C++ QGraphicsScene still owns the item and keeps rendering it.
            if self._map_canvas and self._map_canvas.scene():
                self._map_canvas.scene().removeItem(self._current_highlight)
            del self._current_highlight
            self._current_highlight = None
        self._current_highlight_key = None