# -----------------------------------------------------------
#
# Elevation Profile Widget
# Copyright (C) 2024  TEKSI Contributors
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

from qgis.gui import QgsElevationProfileCanvas
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget
from ..tools.twwnetwork import TwwGraphManager


class TwwElevationProfileWidget(QWidget):
    """
    Widget that wraps QGIS Elevation Profile Canvas for displaying wastewater network profiles.
    
    This widget replaces the old TwwPlotSVGWidget which used QtWebKit.
    """
    
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
        self.canvas = QgsElevationProfileCanvas(self)
        layout.addWidget(self.canvas)
        
        # Store network analyzer for potential future use
        self.networkAnalyzer = network_analyzer
        
        # Vertical exaggeration value (default 10x)
        self.verticalExaggeration = 10.0
        
        # Track if data sources have been set up
        self._data_sources_setup = False
    
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

        
    
    def setupDataSources(self):
        """
        Set up data sources for the elevation profile canvas.
        
        Based on the working configuration tested by the project owner:
        - Use "Capture curve from features" approach
        - Selected layers: vw_tww_reach, vw_wastewater_node, vw_cover, vw_change_points
        - Set all layers to "Absolute" clamping (use Z values from geometry)
        
        Reference: https://docs.qgis.org/3.40/en/docs/user_manual/map_views/elevation_profile.html
        """
        print("=" * 60)
        print("setupDataSources: Starting data source setup...")
        
        from ..utils.twwlayermanager import TwwLayerManager
        from qgis.core import (
            QgsProject, 
            QgsVectorLayerElevationProperties, 
            QgsWkbTypes,
            Qgis,
            QgsLineSymbol,
            QgsFillSymbol,
            QgsMarkerSymbol,
            QgsSimpleLineSymbolLayer,
            QgsSimpleFillSymbolLayer,
            QgsSimpleMarkerSymbolLayer
        )
        from qgis.PyQt.QtGui import QColor
        
        # 1. CRITICAL: Set project first - this is required by QgsElevationProfileCanvas
        project = QgsProject.instance()
        self.canvas.setProject(project)
        print("✓ Project set to canvas")
        
        # 2. Define the layers to use (based on working configuration)
        # These are the layers that were tested and confirmed to work with "Absolute" setting
        # profile_type: 'surface' = Continuous Surface, 'features' = Individual Features
        # Style: (line_color, line_width, fill_color, marker_color, marker_size)
        layer_configs = [
            ('vw_tww_reach', 'Reach/Pipe segments', 'features', 
             {'line': '#2E86AB', 'line_width': 2.5, 'fill': '#2E86AB40', 'marker': '#2E86AB', 'marker_size': 3}),
            ('vw_wastewater_node', 'Wastewater nodes', 'surface',
             {'line': '#A23B72', 'line_width': 1.5, 'fill': '#A23B7230', 'marker': '#A23B72', 'marker_size': 4}),
            ('vw_cover', 'Covers', 'surface',
             {'line': '#F18F01', 'line_width': 1.5, 'fill': '#F18F0130', 'marker': '#F18F01', 'marker_size': 5}),
            ('vw_change_points', 'Change points', 'features',
             {'line': '#C73E1D', 'line_width': 1.0, 'fill': '#C73E1D20', 'marker': '#C73E1D', 'marker_size': 6}),
        ]
        
        layers_to_add = []
        first_valid_crs = None
        
        for layer_name, description, profile_type, style in layer_configs:
            layer = TwwLayerManager.layer(layer_name)
            if not layer:
                print(f"  ⚠ Layer '{layer_name}' ({description}) not found, skipping")
                continue
            
            print(f"  Found layer: {layer_name} ({description})")
            
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
                            print(f"    ✓ Set profile type to Continuous Surface")
                        else:
                            elevation_props.setType(Qgis.VectorProfileType.IndividualFeatures)
                            print(f"    ✓ Set profile type to Individual Features")
                    
                    # Set clamping to Absolute (use geometry Z values)
                    # This is the key setting that makes it work!
                    if hasattr(Qgis, 'AltitudeClamping'):
                        # QGIS 3.26+
                        elevation_props.setClamping(Qgis.AltitudeClamping.Absolute)
                        print(f"    ✓ Set clamping to Absolute (Qgis.AltitudeClamping)")
                    elif hasattr(elevation_props, 'setClamping'):
                        # Try numeric value (0 = Absolute in some versions)
                        elevation_props.setClamping(0)
                        print(f"    ✓ Set clamping to Absolute (numeric)")
                    
                    # Set binding to vertex (for line layers, use vertex Z values)
                    if hasattr(Qgis, 'AltitudeBinding'):
                        elevation_props.setBinding(Qgis.AltitudeBinding.Vertex)
                        print(f"    ✓ Set binding to Vertex")
                    
                    # Check if geometry has Z values
                    has_z = QgsWkbTypes.hasZ(layer.wkbType())
                    print(f"    Geometry has Z: {has_z}")
                    
                    # 4. Configure profile symbols for better appearance
                    self._configureLayerSymbols(elevation_props, style, layer_name)
                    
                    # Force layer to recognize changes
                    layer.triggerRepaint()
                    
                except Exception as e:
                    print(f"    ⚠ Failed to configure elevation properties: {e}")
            else:
                print(f"    ⚠ Layer has no vector elevation properties")
            
            layers_to_add.append(layer)
        
        if not layers_to_add:
            print("✗ No layers found! Available layers in project:")
            for layer_id, layer in project.mapLayers().items():
                print(f"    - {layer.name()}")
            print("=" * 60)
            return
        
        # 4. Set CRS (use CRS from first valid layer)
        if first_valid_crs:
            self.canvas.setCrs(first_valid_crs)
            print(f"✓ CRS set: {first_valid_crs.authid()}")
        
        # 5. Set all configured layers to the canvas
        self.canvas.setLayers(layers_to_add)
        print(f"✓ {len(layers_to_add)} layers set to canvas:")
        for layer in layers_to_add:
            print(f"    - {layer.name()}")
        
        # 6. Set tolerance (distance from profile curve to include features)
        self.canvas.setTolerance(10.0)  # 10 meters
        print(f"✓ Tolerance set: 10.0 meters")
        
        print("=" * 60)
        print("setupDataSources: Complete!")
    
    def _configureLayerSymbols(self, elevation_props, style, layer_name):
        """
        Configure profile symbols for a layer to improve visual appearance.
        
        :param elevation_props: QgsVectorLayerElevationProperties
        :param style: dict with 'line', 'line_width', 'fill', 'marker', 'marker_size'
        :param layer_name: Name of the layer for logging
        """
        from qgis.core import (
            QgsLineSymbol,
            QgsFillSymbol,
            QgsMarkerSymbol,
        )
        from qgis.PyQt.QtGui import QColor
        
        try:
            # Create and set line symbol
            if hasattr(elevation_props, 'setProfileLineSymbol'):
                line_symbol = QgsLineSymbol.createSimple({
                    'color': style['line'],
                    'width': str(style['line_width']),
                    'capstyle': 'round',
                    'joinstyle': 'round'
                })
                elevation_props.setProfileLineSymbol(line_symbol)
                print(f"    ✓ Line symbol set: {style['line']}, width={style['line_width']}")
            
            # Create and set fill symbol (for areas under the profile line)
            if hasattr(elevation_props, 'setProfileFillSymbol'):
                fill_symbol = QgsFillSymbol.createSimple({
                    'color': style['fill'],
                    'outline_color': style['line'],
                    'outline_width': '0.5'
                })
                elevation_props.setProfileFillSymbol(fill_symbol)
                print(f"    ✓ Fill symbol set: {style['fill']}")
            
            # Create and set marker symbol (for points/vertices)
            if hasattr(elevation_props, 'setProfileMarkerSymbol'):
                marker_symbol = QgsMarkerSymbol.createSimple({
                    'color': style['marker'],
                    'size': str(style['marker_size']),
                    'outline_color': '#FFFFFF',
                    'outline_width': '0.5',
                    'name': 'circle'
                })
                elevation_props.setProfileMarkerSymbol(marker_symbol)
                print(f"    ✓ Marker symbol set: {style['marker']}, size={style['marker_size']}")
            
            # Optionally respect layer symbology (use original layer colors)
            # Set to False to use our custom profile symbols instead
            if hasattr(elevation_props, 'setRespectLayerSymbology'):
                elevation_props.setRespectLayerSymbology(False)
                print(f"    ✓ Using custom profile symbols")
                
        except Exception as e:
            print(f"    ⚠ Failed to configure symbols for {layer_name}: {e}")

    def setProfileCurve(self, geometry):
        """
        Set the profile curve (path) for the elevation profile.
        
        :param geometry: QgsGeometry object representing the path
        """
        from qgis.core import QgsGeometry, QgsLineString
        from qgis.PyQt.QtCore import QTimer
        
        if not isinstance(geometry, QgsGeometry) or geometry.isEmpty():
            print(f"✗ setProfileCurve: Invalid or empty geometry")
            return
        
        # Get the points from the geometry
        points = geometry.asPolyline()
        if not points:
            print("✗ setProfileCurve: Geometry has no points")
            return
        
        print(f"✓ setProfileCurve: Received geometry with {len(points)} points")
        
        # Create a new QgsLineString with the points
        curve = QgsLineString(points)
        
        # Ensure data sources are set up before setting the curve
        # This is critical because the canvas needs layers and project to display the profile
        layers_configured = False
        if hasattr(self.canvas, 'layers'):
            layers = self.canvas.layers()
            layers_configured = layers and len(layers) > 0
        
        if not layers_configured or not getattr(self, '_data_sources_setup', False):
            print("  Setting up data sources...")
            self.setupDataSources()
            self._data_sources_setup = True
        
        # Cancel any running jobs before setting new curve
        if hasattr(self.canvas, 'cancelJobs'):
            self.canvas.cancelJobs()
            print("✓ Previous jobs cancelled")
        
        # Invalidate current plot extent before setting new curve
        # This forces the canvas to recalculate everything
        if hasattr(self.canvas, 'invalidateCurrentPlotExtent'):
            self.canvas.invalidateCurrentPlotExtent()
            print("✓ Plot extent invalidated")
        
        # Set the profile curve
        self.canvas.setProfileCurve(curve)
        print(f"✓ Profile curve set to canvas")
        
        # Force refresh to generate the profile
        self.canvas.refresh()
        print(f"✓ Canvas refreshed")
        
        # Use QTimer to delay zoomFull - gives the canvas time to process
        def delayedZoomFull():
            if hasattr(self.canvas, 'zoomFull'):
                try:
                    self.canvas.zoomFull()
                    print(f"✓ zoomFull() called (delayed)")
                except Exception as e:
                    print(f"  ⚠ zoomFull() failed: {e}")
        
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
        from ..utils.twwlayermanager import TwwLayerManager
        from qgis.core import QgsGeometry, QgsFeatureRequest
        
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
            
            # Set up data sources and profile curve
            self.setupDataSources()
            self.setProfileCurve(profile_geometry)