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
        
        # Track if data sources have been set up
        self._data_sources_setup = False
        
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
        print("=" * 60)
        print("setupDataSources: Starting data source setup...")
        
        # Get the reach layer
        # Try vw_tww_reach first (has 3D geometry), fallback to vw_network_segment
        from ..utils.twwlayermanager import TwwLayerManager
        from qgis.core import QgsMapLayerElevationProperties
        
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer:
            print("  vw_tww_reach not found, trying vw_network_segment...")
            # Fallback to vw_network_segment
            reach_layer = TwwLayerManager.layer("vw_network_segment")
        
        if not reach_layer:
            print("✗ setupDataSources: Layer vw_tww_reach or vw_network_segment not found")
            print("  Available layers in project:")
            from qgis.core import QgsProject
            for layer_id, layer in QgsProject.instance().mapLayers().items():
                print(f"    - {layer.name()} (ID: {layer_id})")
            print("=" * 60)
            return
        
        print(f"✓ setupDataSources: Found layer {reach_layer.name()} (ID: {reach_layer.id()})")
        
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
                sample_elevation_values = []  # Collect sample elevation values
                for field_name in elevation_fields:
                    if field_name in reach_layer.fields().names():
                        value = sample_feature.attribute(field_name)
                        print(f"  Sample feature {field_name} field value: {value}")
                        if value is not None:
                            found_elevation = True
                            try:
                                sample_elevation_values.append(float(value))
                            except:
                                pass
                if not found_elevation:
                    print(f"  ⚠ All elevation fields are NULL, available fields: {[f.name() for f in reach_layer.fields()]}")
                elif sample_elevation_values:
                    # Store sample elevation values for later use
                    self._sample_elevation_values = sample_elevation_values
                    print(f"  ✓ Collected {len(sample_elevation_values)} sample elevation values: {sample_elevation_values}")
        
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
                        # Verify it was enabled
                        if hasattr(elevation_props, 'isEnabled'):
                            verify_enabled = elevation_props.isEnabled()
                            print(f"  ✓ Verified elevation is enabled: {verify_enabled}")
                    else:
                        print("  ✓ Layer elevation already enabled")
                except Exception as e:
                    print(f"  ⚠ Failed to enable elevation: {e}")
                    import traceback
                    traceback.print_exc()
            
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
        try:
            self.canvas.setLayers([reach_layer])
            print(f"✓ Layer set to canvas: {reach_layer.name()}")
            
            # Verify the layer was actually set
            if hasattr(self.canvas, 'layers'):
                canvas_layers = self.canvas.layers()
                if canvas_layers and reach_layer in canvas_layers:
                    print(f"✓ Verified: Layer is correctly set on canvas")
                else:
                    print(f"⚠ WARNING: Layer may not be correctly set on canvas")
                    print(f"  Canvas layers: {[l.name() for l in (canvas_layers or [])]}")
        except Exception as e:
            print(f"✗ Error setting layers to canvas: {e}")
            import traceback
            traceback.print_exc()
        
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
        
        # Always collect elevation data from the layer (even without profile curve)
        # This helps set the correct elevation range
        try:
            from qgis.core import QgsFeatureRequest
            
            # First, try to get elevation from a sample of features (without profile curve filter)
            # This gives us a baseline elevation range
            print("  Collecting elevation data from layer features...")
            sample_request = QgsFeatureRequest()
            sample_request.setLimit(50)  # Sample first 50 features
            
            sample_elevation_values = []
            sample_count = 0
            for feature in reach_layer.getFeatures(sample_request):
                sample_count += 1
                # Check elevation fields
                rp_from_level = feature.attribute('rp_from_level') if 'rp_from_level' in reach_layer.fields().names() else None
                rp_to_level = feature.attribute('rp_to_level') if 'rp_to_level' in reach_layer.fields().names() else None
                bottom_level = feature.attribute('bottom_level') if 'bottom_level' in reach_layer.fields().names() else None
                
                for level in [rp_from_level, rp_to_level, bottom_level]:
                    if level is not None:
                        try:
                            level_float = float(level)
                            if level_float != 0.0:  # Ignore zero values
                                sample_elevation_values.append(level_float)
                        except:
                            pass
                
                if len(sample_elevation_values) >= 20:  # Collect enough values
                    break
            
            if sample_elevation_values:
                min_elev = min(sample_elevation_values)
                max_elev = max(sample_elevation_values)
                elev_range = max_elev - min_elev
                # Add 20% padding
                elevation_min = min_elev - elev_range * 0.2
                elevation_max = max_elev + elev_range * 0.2
                print(f"  ✓ Sample elevation range from {sample_count} features: {min_elev:.2f} - {max_elev:.2f}")
                print(f"  ✓ Suggested elevation range with padding: {elevation_min:.2f} - {elevation_max:.2f}")
                self._suggested_elevation_range = (elevation_min, elevation_max)
                self._sample_elevation_values = sample_elevation_values
            else:
                print(f"  ⚠ No elevation values found in sampled features")
            
            # Now, if profile curve is set, refine the elevation range based on features near the curve
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
                        print(f"  Actual Z value range near profile: {min_z:.2f} - {max_z:.2f}, suggested elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
                        # Update the suggested range with more accurate values from profile curve
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
                            print(f"  Elevation range from fields near profile: {min_elev:.2f} - {max_elev:.2f}, suggested elevation range: {elevation_min:.2f} - {elevation_max:.2f}")
                            # Update the suggested range with more accurate values from profile curve
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
        
        print(f"✓ setProfileCurve: Called with geometry type: {type(geometry)}")
        
        if isinstance(geometry, QgsGeometry) and not geometry.isEmpty():
            # Get the points from the geometry
            points = geometry.asPolyline()
            
            if points:
                print(f"✓ setProfileCurve: Received geometry with {len(points)} points")
                print(f"  First point: {points[0]}, last point: {points[-1]}")
                
                # Create a new QgsLineString with the points
                curve = QgsLineString(points)
                
                # ALWAYS ensure data sources are set up before setting the curve
                # This is critical because the canvas needs layers to display the profile
                print(f"  Checking data sources setup status: _data_sources_setup = {getattr(self, '_data_sources_setup', 'NOT SET')}")
                
                # Verify layers are set on canvas FIRST
                layers_configured = False
                if hasattr(self.canvas, 'layers'):
                    layers = self.canvas.layers()
                    print(f"  Canvas currently has {len(layers) if layers else 0} layers configured")
                    if layers and len(layers) > 0:
                        layers_configured = True
                        for layer in layers:
                            print(f"    - Layer: {layer.name()} (ID: {layer.id()})")
                
                # If no layers or data sources not set up, set them up now
                if not layers_configured or not getattr(self, '_data_sources_setup', False):
                    print("  Setting up data sources (layers not configured or first time)...")
                    self.setupDataSources()
                    self._data_sources_setup = True
                    print("  Data sources setup completed")
                    
                    # Verify again after setup
                    if hasattr(self.canvas, 'layers'):
                        layers = self.canvas.layers()
                        print(f"  After setup: Canvas has {len(layers) if layers else 0} layers configured")
                        if layers:
                            for layer in layers:
                                print(f"    - Layer: {layer.name()} (ID: {layer.id()})")
                        else:
                            print("  ⚠ WARNING: Canvas still has no layers after setup!")
                else:
                    print("  Data sources already set up, skipping...")
                
                # Set the profile curve
                self.canvas.setProfileCurve(curve)
                print(f"✓ Profile curve set to canvas")
                
                # Verify the curve was set
                if hasattr(self.canvas, 'profileCurve'):
                    curve_check = self.canvas.profileCurve() if callable(self.canvas.profileCurve) else self.canvas.profileCurve
                    if curve_check:
                        print(f"✓ Verified: Profile curve is set on canvas")
                        # Check features near the profile curve
                        self._checkFeaturesNearProfileCurve()
                    else:
                        print(f"⚠ Warning: Profile curve may not be set correctly")
                
                # Calculate and set visible plot range
                self._setInitialVisibleRange()
                
                # Force refresh after setting everything
                self.canvas.refresh()
                print(f"✓ Canvas refreshed (after setting profile curve)")
                
                # Try to trigger profile generation if method exists
                if hasattr(self.canvas, 'generateProfile'):
                    try:
                        print("  Attempting to generate profile...")
                        self.canvas.generateProfile()
                        print("  ✓ Profile generation triggered")
                    except Exception as e:
                        print(f"  ⚠ generateProfile() failed: {e}")
                
                # Check canvas size (might be 0 if widget not shown yet)
                canvas_size = self.canvas.size()
                print(f"  Canvas size: {canvas_size.width()} x {canvas_size.height()}")
                if canvas_size.width() == 0 or canvas_size.height() == 0:
                    print("  ⚠ WARNING: Canvas size is 0! Widget may not be visible yet.")
                    print("     Profile may not display until widget is shown.")
                
                # Additional verification
                print("  Final verification:")
                if hasattr(self.canvas, 'layers'):
                    layers = self.canvas.layers()
                    print(f"    - Canvas layers: {len(layers) if layers else 0}")
                if hasattr(self.canvas, 'profileCurve'):
                    curve_final = self.canvas.profileCurve() if callable(self.canvas.profileCurve) else self.canvas.profileCurve
                    print(f"    - Profile curve set: {curve_final is not None}")
                if hasattr(self.canvas, 'visiblePlotRange'):
                    try:
                        range_info = self.canvas.visiblePlotRange() if callable(self.canvas.visiblePlotRange) else self.canvas.visiblePlotRange
                        print(f"    - Visible plot range: {range_info}")
                    except:
                        pass
                
                # Check if widget is visible
                if hasattr(self, 'isVisible'):
                    print(f"    - Widget visible: {self.isVisible()}")
                if hasattr(self.canvas, 'isVisible'):
                    print(f"    - Canvas visible: {self.canvas.isVisible()}")
            else:
                print("✗ setProfileCurve: Geometry is empty (no points)")
        else:
            print(f"✗ setProfileCurve: Invalid geometry object - {type(geometry)}, isEmpty: {geometry.isEmpty() if isinstance(geometry, QgsGeometry) else 'N/A'}")
    
    def _checkFeaturesNearProfileCurve(self):
        """
        Check if there are features with elevation data near the profile curve.
        This helps diagnose why the profile might not be displaying.
        """
        from ..utils.twwlayermanager import TwwLayerManager
        from qgis.core import QgsFeatureRequest, QgsWkbTypes
        
        reach_layer = TwwLayerManager.layer("vw_tww_reach")
        if not reach_layer:
            reach_layer = TwwLayerManager.layer("vw_network_segment")
        
        if not reach_layer:
            print("  ⚠ Cannot check features: layer not found")
            return
        
        try:
            if hasattr(self.canvas, 'profileCurve'):
                curve = self.canvas.profileCurve() if callable(self.canvas.profileCurve) else self.canvas.profileCurve
                if not curve:
                    print("  ⚠ Cannot check features: profile curve not set")
                    return
                
                tolerance = self.canvas.tolerance() if hasattr(self.canvas, 'tolerance') else 10.0
                bbox = curve.boundingBox().buffered(tolerance)
                request = QgsFeatureRequest()
                request.setFilterRect(bbox)
                
                print(f"  Checking features near profile curve (tolerance: {tolerance}m)...")
                feature_count = 0
                elevation_count = 0
                elevation_values = []
                
                for feature in reach_layer.getFeatures(request):
                    feature_count += 1
                    
                    # Check if feature has elevation
                    geom = feature.geometry()
                    has_3d_geom = geom and QgsWkbTypes.hasZ(geom.wkbType())
                    
                    # Check elevation fields
                    rp_from_level = feature.attribute('rp_from_level') if 'rp_from_level' in reach_layer.fields().names() else None
                    rp_to_level = feature.attribute('rp_to_level') if 'rp_to_level' in reach_layer.fields().names() else None
                    bottom_level = feature.attribute('bottom_level') if 'bottom_level' in reach_layer.fields().names() else None
                    
                    if has_3d_geom or any([rp_from_level, rp_to_level, bottom_level]):
                        elevation_count += 1
                        
                        # Collect elevation values
                        if has_3d_geom:
                            try:
                                if geom.type() == QgsWkbTypes.LineGeometry:
                                    from qgis.core import QgsPoint
                                    polyline = geom.asPolyline()
                                    z_vals = [p.z() for p in polyline if isinstance(p, QgsPoint) and p.is3D()]
                                    if z_vals:
                                        elevation_values.extend(z_vals)
                            except:
                                pass
                        
                        for level in [rp_from_level, rp_to_level, bottom_level]:
                            if level is not None:
                                try:
                                    elevation_values.append(float(level))
                                except:
                                    pass
                
                print(f"    - Found {feature_count} features near profile curve")
                print(f"    - {elevation_count} features have elevation data")
                
                if elevation_values:
                    min_elev = min(elevation_values)
                    max_elev = max(elevation_values)
                    elev_range = max_elev - min_elev
                    # Add 20% padding
                    elevation_min = min_elev - elev_range * 0.2
                    elevation_max = max_elev + elev_range * 0.2
                    print(f"    - Elevation range near curve: {min_elev:.2f} - {max_elev:.2f}")
                    print(f"    - Suggested range with padding: {elevation_min:.2f} - {elevation_max:.2f}")
                    
                    # Update the suggested elevation range with values from features near the curve
                    # This is more accurate than the general layer sampling
                    self._suggested_elevation_range = (elevation_min, elevation_max)
                    print(f"    - ✓ Updated suggested elevation range based on features near curve")
                else:
                    print(f"    - ⚠ WARNING: No elevation values found near profile curve!")
                    print(f"      This may be why the profile is not displaying.")
        except Exception as e:
            print(f"  ⚠ Error checking features near profile curve: {e}")
            import traceback
            traceback.print_exc()
    
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