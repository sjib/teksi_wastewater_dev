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
from qgis.core import QgsWkbTypes
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
        
        # Set initial visible range immediately when widget is created
        self._setInitialVisibleRange()

        # Set up data sources (optional - can be called later)
        # self.setupDataSources()
    
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
        
        This method sets up the reach layer as a data source for the profile.
        In QGIS 3.40+, we use setLayers() directly instead of ProfileSource objects.
        """
        # Get the reach layer
        # Try vw_tww_reach first (has 3D geometry), fallback to vw_network_segment
        from ..utils.twwlayermanager import TwwLayerManager
        from qgis.core import QgsMapLayerElevationProperties
        
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer:
            # Fallback to vw_network_segment
            reach_layer = TwwLayerManager.layer("vw_network_segment")
        
        if not reach_layer:
            print("✗ setupDataSources: Layer vw_tww_reach or vw_network_segment not found")
            return
        
        print(f"✓ setupDataSources: Found layer {reach_layer.name()}")
        
        # Check geometry type and sample data
        from qgis.core import QgsWkbTypes
        print(f"  Layer geometry type: {reach_layer.wkbType()}")
        print(f"  Is 3D geometry: {QgsWkbTypes.hasZ(reach_layer.wkbType())}")
        
        # Check a sample feature to see if geometry has Z values
        sample_feature = next(reach_layer.getFeatures(), None)
        if sample_feature:
            geom = sample_feature.geometry()
            if geom:
                print(f"  Sample feature geometry type: {geom.wkbType()}")
                is_3d = QgsWkbTypes.hasZ(geom.wkbType())
                print(f"  Sample feature is 3D: {is_3d}")
                if is_3d:
                    # Try to get Z values
                    try:
                        if geom.type() == QgsWkbTypes.LineGeometry:
                            polyline = geom.asPolyline()
                            if polyline:
                                # Check if points have Z (QgsPointXY doesn't have z(), QgsPoint does)
                                from qgis.core import QgsPoint
                                z_values = []
                                for p in polyline[:5]:  # Only check first 5 points
                                    if isinstance(p, QgsPoint) and p.is3D():
                                        z_values.append(p.z())
                                if z_values:
                                    print(f"  Sample feature Z values: {z_values}")
                    except:
                        pass
                # Check elevation field values (check multiple possible field names)
                elevation_fields = ['bottom_level', 'rp_from_level', 'rp_to_level', 'level', 'elevation']
                found_elevation = False
                for field_name in elevation_fields:
                    if field_name in reach_layer.fields().names():
                        value = sample_feature.attribute(field_name)
                        print(f"  Sample feature {field_name} field value: {value}")
                        if value is not None:
                            found_elevation = True
                if not found_elevation:
                    print(f"  ⚠ All elevation fields are NULL, available fields: {[f.name() for f in reach_layer.fields()]}")
        
        # Check and configure elevation properties
        elevation_props = reach_layer.elevationProperties()
        if elevation_props:
            print(f"  Layer elevation properties type: {type(elevation_props).__name__}")
            
            # Check if elevation is enabled
            is_enabled = False
            if hasattr(elevation_props, 'isEnabled'):
                try:
                    is_enabled = elevation_props.isEnabled()
                    print(f"  Elevation current status: {'Enabled' if is_enabled else 'Disabled'}")
                except:
                    pass
            
            # Enable elevation if not already enabled
            if hasattr(elevation_props, 'setEnabled'):
                try:
                    if not is_enabled:
                        elevation_props.setEnabled(True)
                        print("  ✓ Layer elevation enabled")
                        # Force layer to recognize the change
                        reach_layer.triggerRepaint()
                    else:
                        print("  ✓ Layer elevation already enabled")
                except Exception as e:
                    print(f"  ⚠ Failed to enable elevation: {e}")
            
            # For QgsVectorLayerElevationProperties, check current configuration
            from qgis.core import QgsVectorLayerElevationProperties
            
            if isinstance(elevation_props, QgsVectorLayerElevationProperties):
                # Check current mode and field
                try:
                    if hasattr(elevation_props, 'mode'):
                        mode = elevation_props.mode
                        print(f"  Current elevation mode: {mode}")
                    if hasattr(elevation_props, 'elevationField'):
                        # Try to read it as a property
                        try:
                            field_name = elevation_props.elevationField
                            print(f"  Current elevation field (property): {field_name}")
                        except:
                            # Try as a method
                            try:
                                field = elevation_props.elevationField()
                                print(f"  Current elevation field (method): {field.name() if field else 'None'}")
                            except:
                                pass
                except Exception as e:
                    print(f"  ⚠ Failed to read elevation configuration: {e}")
                
                # If geometry is not 3D, we need to configure elevation from attribute field
                if not QgsWkbTypes.hasZ(reach_layer.wkbType()):
                    print("  ⚠ Layer geometry is not 3D, need to read elevation from attribute field")
                    fields = reach_layer.fields()
                    bottom_level_idx = fields.indexOf('bottom_level')
                    if bottom_level_idx >= 0:
                        field = fields.at(bottom_level_idx)
                        print(f"  Found bottom_level field, index: {bottom_level_idx}")
                        
                        # Try to set elevation field using different methods
                        try:
                            # Method 1: Direct property assignment
                            elevation_props.elevationField = field.name()
                            print(f"  ✓ Set elevation field to: {field.name()} (property assignment)")
                        except Exception as e1:
                            print(f"  ✗ Method 1 failed: {e1}")
                            # Method 2: Try using QGIS layer style to configure
                            # This might require using QgsMapLayerStyle or other approach
                            try:
                                # Maybe we need to trigger a layer style update
                                reach_layer.triggerRepaint()
                                print(f"  ✓ Triggered layer repaint")
                            except:
                                pass
                else:
                    print("  ✓ Layer geometry is 3D, should be able to read elevation from Z coordinates")
                    # For 3D geometry, ensure mode is set to use Z coordinates
                    try:
                        # Check available attributes and methods
                        available_attrs = [attr for attr in dir(elevation_props) if not attr.startswith('_')]
                        print(f"  Elevation properties available attributes/methods: {available_attrs[:10]}...")  # Show first 10
                        
                        # Try setting mode directly if available
                        if hasattr(elevation_props, 'mode'):
                            try:
                                # Check current mode
                                current_mode = elevation_props.mode
                                print(f"  Current elevation mode: {current_mode}")
                                # Mode 2 = Z coordinates (if that's the enum value)
                                # Try different possible values
                                try:
                                    elevation_props.mode = 2  # Try mode 2
                                    print("  ✓ Set elevation mode to 2 (Z coordinates)")
                                except:
                                    # Try other possible values
                                    try:
                                        from qgis.core import QgsVectorLayerElevationProperties
                                        # Check if there's an enum for mode
                                        if hasattr(QgsVectorLayerElevationProperties, 'ElevationMode'):
                                            # Try Z mode
                                            elevation_props.mode = QgsVectorLayerElevationProperties.ElevationMode.Z
                                            print("  ✓ Set elevation mode to Z coordinates (enum)")
                                    except Exception as e2:
                                        print(f"  ⚠ Failed to set mode: {e2}")
                            except Exception as e1:
                                print(f"  ⚠ Failed to read/set mode: {e1}")
                        
                        # Try setClamping if available
                        if hasattr(elevation_props, 'setClamping'):
                            try:
                                # Check what clamping values are available
                                if hasattr(elevation_props, 'Clamping'):
                                    clamping_attrs = [attr for attr in dir(elevation_props.Clamping) if not attr.startswith('_')]
                                    print(f"  Clamping enum values: {clamping_attrs}")
                            except:
                                pass
                    except Exception as e:
                        print(f"  ⚠ Failed to set 3D elevation mode: {e}")
                        import traceback
                        traceback.print_exc()
        else:
            print("  ⚠ Layer has no elevation properties")
        
        # In QGIS 3.40+, we can directly set layers using setLayers()
        # The canvas will automatically use the layer's elevation configuration
        self.canvas.setLayers([reach_layer])
        print(f"✓ Layer set to canvas")
        
        # Set tolerance (distance from profile curve to include features, in map units)
        self.canvas.setTolerance(10.0)  # 10 meters (adjust as needed)
        print(f"✓ Tolerance set: 10.0 meters")
        
        # Force canvas to refresh and regenerate the profile
        # This is important for 3D layers to display correctly
        self.canvas.refresh()
        print(f"✓ Canvas refreshed")
        
        # Set CRS if not already set (optional, canvas may auto-detect from layers)
        if reach_layer.crs().isValid():
            self.canvas.setCrs(reach_layer.crs())
            print(f"✓ CRS set: {reach_layer.crs().authid()}")
        
        # Check what layers are actually set
        if hasattr(self.canvas, 'layers'):
            layers = self.canvas.layers()
            print(f"  Canvas current layer count: {len(layers) if layers else 0}")
        
        # Check if profile curve is set and verify features near it have elevation data
        try:
            from qgis.core import QgsFeatureRequest
            # profileCurve might be a method, try calling it
            if hasattr(self.canvas, 'profileCurve'):
                curve = self.canvas.profileCurve() if callable(self.canvas.profileCurve) else self.canvas.profileCurve
                if curve:
                    bbox = curve.boundingBox().buffered(self.canvas.tolerance())
                    request = QgsFeatureRequest()
                    request.setFilterRect(bbox)
                    
                    # Check a few features near the profile curve and collect Z values
                    feature_count = 0
                    elevation_count = 0
                    all_z_values = []  # Collect all Z values to calculate range
                    for feature in reach_layer.getFeatures(request):
                        feature_count += 1
                        if feature_count > 10:  # Check more features to get better Z range
                            break
                        
                        # Check if feature has elevation (either 3D geometry or elevation field)
                        geom = feature.geometry()
                        if geom and QgsWkbTypes.hasZ(geom.wkbType()):
                            elevation_count += 1
                            # Try to get actual Z values
                            try:
                                if geom.type() == QgsWkbTypes.LineGeometry:
                                    # Try to get Z values from 3D geometry
                                    z_values = []
                                    from qgis.core import QgsPoint
                                    try:
                                        # Method 1: Try asPolyline3D()
                                        polyline_3d = geom.asPolyline3D()
                                        if polyline_3d:
                                            z_values = [p.z() for p in polyline_3d if isinstance(p, QgsPoint) and p.is3D()]
                                    except AttributeError:
                                        # Method 2: Try constGet() to get the actual curve
                                        try:
                                            curve = geom.constGet()
                                            if hasattr(curve, 'points'):
                                                points = curve.points()
                                                z_values = [p.z() for p in points if isinstance(p, QgsPoint) and p.is3D()]
                                            elif hasattr(curve, 'xAt') and hasattr(curve, 'yAt') and hasattr(curve, 'zAt'):
                                                # For QgsLineString, try zAt()
                                                try:
                                                    num_points = curve.numPoints()
                                                    z_values = [curve.zAt(i) for i in range(num_points)]
                                                except:
                                                    pass
                                        except Exception as e2:
                                            if feature_count <= 5:
                                                print(f"  ⚠ Method 2 failed: {e2}")
                                    except Exception as e1:
                                        if feature_count <= 5:
                                            print(f"  ⚠ Method 1 failed: {e1}")
                                    
                                    if z_values:
                                        all_z_values.extend(z_values)
                                        if feature_count <= 5:  # Only print first 5
                                            print(f"  Feature {feature.id()} has 3D geometry, Z value range: {min(z_values):.2f} - {max(z_values):.2f}")
                                    else:
                                        if feature_count <= 5:
                                            print(f"  Feature {feature.id()} has 3D geometry, but unable to read Z values (tried multiple methods)")
                                else:
                                    if feature_count <= 5:
                                        print(f"  Feature {feature.id()} has 3D geometry, type: {geom.type()}")
                            except Exception as e:
                                if feature_count <= 5:
                                    print(f"  Feature {feature.id()} has 3D geometry, but failed to read Z values: {e}")
                                    import traceback
                                    traceback.print_exc()
                        elif hasattr(feature, 'attribute'):
                            # Check elevation field
                            bottom_level = feature.attribute('bottom_level') if 'bottom_level' in reach_layer.fields().names() else None
                            rp_from_level = feature.attribute('rp_from_level') if 'rp_from_level' in reach_layer.fields().names() else None
                            rp_to_level = feature.attribute('rp_to_level') if 'rp_to_level' in reach_layer.fields().names() else None
                            if bottom_level is not None or rp_from_level is not None or rp_to_level is not None:
                                elevation_count += 1
                                # Collect elevation values from fields as fallback
                                field_elevations = []
                                if bottom_level is not None:
                                    try:
                                        field_elevations.append(float(bottom_level))
                                    except:
                                        pass
                                if rp_from_level is not None:
                                    try:
                                        field_elevations.append(float(rp_from_level))
                                    except:
                                        pass
                                if rp_to_level is not None:
                                    try:
                                        field_elevations.append(float(rp_to_level))
                                    except:
                                        pass
                                if field_elevations:
                                    all_z_values.extend(field_elevations)
                                if feature_count <= 5:
                                    print(f"  Feature {feature.id()} has elevation field values: bottom_level={bottom_level}, rp_from_level={rp_from_level}, rp_to_level={rp_to_level}")
                    
                    print(f"  Checked {feature_count} features, {elevation_count} have elevation data")
                    
                    # Filter out zero Z values (they might be invalid)
                    valid_z_values = [z for z in all_z_values if z != 0.0 and not (isinstance(z, float) and (z != z or z == float('inf') or z == float('-inf')))]
                    
                    # If we collected valid Z values, calculate and suggest elevation range
                    if valid_z_values:
                        min_z = min(valid_z_values)
                        max_z = max(valid_z_values)
                        z_range = max_z - min_z
                        # Add 10% padding
                        elevation_min = min_z - z_range * 0.1
                        elevation_max = max_z + z_range * 0.1
                        print(f"  Actual Z value range: {min_z:.2f} - {max_z:.2f}, suggested elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
                        # Store for later use in _setInitialVisibleRange
                        self._suggested_elevation_range = (elevation_min, elevation_max)
                    else:
                        # If no valid Z values, try to get from elevation fields
                        print(f"  ⚠ Z values invalid (all zeros), trying to get values from elevation fields")
                        field_elevations = []
                        for feature in reach_layer.getFeatures(request):
                            rp_from_level = feature.attribute('rp_from_level') if 'rp_from_level' in reach_layer.fields().names() else None
                            rp_to_level = feature.attribute('rp_to_level') if 'rp_to_level' in reach_layer.fields().names() else None
                            bottom_level = feature.attribute('bottom_level') if 'bottom_level' in reach_layer.fields().names() else None
                            
                            for level in [rp_from_level, rp_to_level, bottom_level]:
                                if level is not None:
                                    try:
                                        level_float = float(level)
                                        if level_float != 0.0:  # Ignore zero values
                                            field_elevations.append(level_float)
                                    except:
                                        pass
                            
                            if len(field_elevations) >= 10:  # Collect enough values
                                break
                        
                        if field_elevations:
                            min_elev = min(field_elevations)
                            max_elev = max(field_elevations)
                            elev_range = max_elev - min_elev
                            # Add 10% padding
                            elevation_min = min_elev - elev_range * 0.1
                            elevation_max = max_elev + elev_range * 0.1
                            print(f"  Elevation range from fields: {min_elev:.2f} - {max_elev:.2f}, suggested elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
                            self._suggested_elevation_range = (elevation_min, elevation_max)
                        else:
                            print(f"  ⚠ Unable to get elevation values from fields, using default range")
                            self._suggested_elevation_range = None
        except Exception as e:
            print(f"  ⚠ Failed to check feature elevation data: {e}")
            import traceback
            traceback.print_exc()
        
        # Set visible range after setting up data sources
        self._setInitialVisibleRange()

    def setProfileCurve(self, geometry):
        """
        Set the profile curve (path) for the elevation profile.
        
        :param geometry: QgsGeometry object representing the path
        """
        from qgis.core import QgsGeometry, QgsLineString
        
        if isinstance(geometry, QgsGeometry) and not geometry.isEmpty():
            # Get the points from the geometry
            points = geometry.asPolyline()
            
            if points:
                print(f"✓ setProfileCurve: Received geometry with {len(points)} points")
                print(f"  First point: {points[0]}, last point: {points[-1]}")
                
                # Create a new QgsLineString with the points
                curve = QgsLineString(points)
                self.canvas.setProfileCurve(curve)
                print(f"✓ Profile curve set to canvas")
                
                # Set up data sources (safe to call multiple times)
                self.setupDataSources()
                
                # Calculate and set visible plot range
                self._setInitialVisibleRange()
                
                # Force refresh after setting everything
                self.canvas.refresh()
                print(f"✓ Canvas refreshed (after setting profile curve)")
            else:
                print("✗ setProfileCurve: Geometry is empty (no points)")
        else:
            print(f"✗ setProfileCurve: Invalid geometry object - {type(geometry)}")
    
    def _setInitialVisibleRange(self):
        """
        Set the initial visible range for the elevation profile canvas.
        Uses fixed default ranges: X-axis 0-400m, Y-axis 2500-2600m
        
        Note: setVisiblePlotRange requires 4 numeric parameters:
        (distance_min, distance_max, elevation_min, elevation_max)
        """
        # Fixed distance range (X-axis): 0-400m
        distance_min = 0.0
        distance_max = 400.0
        
        # Elevation range (Y-axis): Use suggested range if available, otherwise use sample values or fixed range
        if hasattr(self, '_suggested_elevation_range') and self._suggested_elevation_range:
            elevation_min, elevation_max = self._suggested_elevation_range
            print(f"  Using suggested elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
        elif hasattr(self, '_sample_elevation_values') and self._sample_elevation_values:
            # Use sample elevation values from fields
            min_elev = min(self._sample_elevation_values)
            max_elev = max(self._sample_elevation_values)
            elev_range = max_elev - min_elev
            # Add 20% padding
            elevation_min = min_elev - elev_range * 0.2
            elevation_max = max_elev + elev_range * 0.2
            print(f"  Using sample elevation field value range: {elevation_min:.2f} - {elevation_max:.2f}")
        else:
            # Fixed elevation range (Y-axis): 2500-2600m (fallback)
            elevation_min = 2500.0
            elevation_max = 2600.0
            print(f"  Using default elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
        
        try:
            # setVisiblePlotRange requires 4 numeric parameters, not QgsDoubleRange objects
            if hasattr(self.canvas, 'setVisiblePlotRange'):
                self.canvas.setVisiblePlotRange(distance_min, distance_max, elevation_min, elevation_max)
                # Refresh the canvas to apply the changes
                self.canvas.refresh()
        except Exception as e:
            print(f"✗ Error setting visible range: {e}")
            import traceback
            traceback.print_exc()

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