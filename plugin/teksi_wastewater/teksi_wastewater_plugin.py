# -----------------------------------------------------------
#
# TEKSI Wastewater
#
# Copyright (C) 2012  Matthias Kuhn
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
# with this progsram; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# ---------------------------------------------------------------------


import logging
import os
import shutil

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QLocale, QSettings, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication, QMessageBox, QToolBar
from qgis.utils import qgsfunction

try:
    from .gui.twwplotsvgwidget import TwwPlotSVGWidget
except ImportError:
    TwwPlotSVGWidget = None

# Import the new elevation profile widget
from .gui.twwelevationprofilewidget import TwwElevationProfileWidget


from .gui.twwprofiledockwidget import TwwProfileDockWidget
from .gui.twwsettingsdialog import TwwSettingsDialog
from .gui.twwwizard import TwwWizard
from .libs.modelbaker.iliwrapper.ili2dbutils import JavaNotFoundError
from .processing_provider.provider import TwwProcessingProvider
from .tools.twwmaptools import TwwMapToolConnectNetworkElements, TwwTreeMapTool,TwwProfileMapTool
from .tools.twwnetwork import TwwGraphManager
from .utils.database_utils import DatabaseUtils
from .utils.plugin_utils import plugin_root_path
from .utils.qt_utils import OverrideCursor
from .utils.translation import setup_i18n
from .utils.twwlayermanager import TwwLayerManager, TwwLayerNotifier
from .utils.twwlogging import TwwQgsLogHandler

LOGFORMAT = "%(asctime)s:%(levelname)s:%(module)s:%(message)s"


@qgsfunction(0, "System")
def locale(values, feature, parent):
    return QSettings().value("locale/userLocale", QLocale.system().name())


class TeksiWastewaterPlugin:
    """
    A plugin for wastewater management
    https://github.com/teksi/wastewater
    """

    # The networkAnalyzer will manage the networklayers and pathfinding
    network_analyzer = None

    # Remember not to reopen the dock if there's already one opened
    profile_dock = None

    # Wizard
    wizarddock = None

    # The layer ids the plugin will need
    edgeLayer = None
    nodeLayer = None
    specialStructureLayer = None
    networkElementLayer = None

    profile = None

    def __init__(self, iface):
        if os.environ.get("QGIS_DEBUGPY_HAS_LOADED") is None and QSettings().value(
            "/TWW/DeveloperMode", False, type=bool
        ):
            try:
                import debugpy

                debugpy.configure(python=shutil.which("python"))
                debugpy.listen(("localhost", 5678))
            except Exception as e:
                print(f"Unable to create debugpy debugger: {e}")
            else:
                os.environ["QGIS_DEBUGPY_HAS_LOADED"] = "1"

        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.nodes = None
        self.edges = None

        self.interlisImporterExporter = None

        self.initLogger()
        setup_i18n()

    def tr(self, source_text):
        """
        This does not inherit from QObject but for the translation to work (in particular to have translatable strings
        picked up) we need a tr method.
        :rtype : unicode
        :param source_text: The text to translate
        :return: The translated text
        """
        return QApplication.translate("TeksiWastewaterPlugin", source_text)

    def initLogger(self):
        """
        Initializes the logger
        """
        self.logger = logging.getLogger(__package__)

        settings = QSettings()

        loglevel = settings.value("/TWW/LogLevel", "Warning")
        logfile = settings.value("/TWW/LogFile", None)

        if hasattr(self.logger, "twwFileHandler"):
            self.logger.removeHandler(self.logger.twwFileHandler)
            del self.logger.twwFileHandler

        current_handlers = [h.__class__.__name__ for h in self.logger.handlers]
        if self.__class__.__name__ not in current_handlers:
            self.logger.addHandler(TwwQgsLogHandler())

        if logfile:
            log_handler = logging.FileHandler(logfile)
            fmt = logging.Formatter(LOGFORMAT)
            log_handler.setFormatter(fmt)
            self.logger.addHandler(log_handler)
            self.logger.fileHandler = log_handler

        if "Debug" == loglevel:
            self.logger.setLevel(logging.DEBUG)
        elif "Info" == loglevel:
            self.logger.setLevel(logging.INFO)
        elif "Warning" == loglevel:
            self.logger.setLevel(logging.WARNING)
        elif "Error" == loglevel:
            self.logger.setLevel(logging.ERROR)

        fp = os.path.join(os.path.abspath(os.path.dirname(__file__)), "metadata.txt")

        ini_text = QSettings(fp, QSettings.IniFormat)
        verno = ini_text.value("version")

        self.logger.info("TEKSI Wastewater plugin version " + verno + " started")

    def initGui(self):
        """
        Called to setup the plugin GUI
        """
        self.network_layer_notifier = TwwLayerNotifier(
            self.iface.mainWindow(),
            ["vw_network_node", "vw_network_segment"],
        )
        self.vw_tww_layer_notifier = TwwLayerNotifier(
            self.iface.mainWindow(),
            ["vw_tww_wastewater_structure"],
        )
        self.toolbarButtons = []

        # Create toolbar button
        self.profileAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/wastewater-profile.svg")),
            self.tr("Profile"),
            self.iface.mainWindow(),
        )
        self.profileAction.setWhatsThis(self.tr("Reach trace"))
        self.profileAction.setEnabled(False)
        self.profileAction.setCheckable(True)
        self.profileAction.triggered.connect(self.profileToolClicked)

        self.downstreamAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/wastewater-downstream.svg")),
            self.tr("Downstream"),
            self.iface.mainWindow(),
        )
        self.downstreamAction.setWhatsThis(self.tr("Downstream reaches"))
        self.downstreamAction.setEnabled(False)
        self.downstreamAction.setCheckable(True)
        self.downstreamAction.triggered.connect(self.downstreamToolClicked)

        self.upstreamAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/wastewater-upstream.svg")),
            self.tr("Upstream"),
            self.iface.mainWindow(),
        )
        self.upstreamAction.setWhatsThis(self.tr("Upstream reaches"))
        self.upstreamAction.setEnabled(False)
        self.upstreamAction.setCheckable(True)
        self.upstreamAction.triggered.connect(self.upstreamToolClicked)

        self.wizardAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/wizard.svg")),
            "Wizard",
            self.iface.mainWindow(),
        )
        self.wizardAction.setWhatsThis(self.tr("Create new manholes and reaches"))
        self.wizardAction.setEnabled(False)
        self.wizardAction.setCheckable(True)
        self.wizardAction.triggered.connect(self.wizard)

        self.connectNetworkElementsAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/link-wastewater-networkelement.svg")),
            QApplication.translate("teksi_wastewater", "Connect wastewater networkelements"),
            self.iface.mainWindow(),
        )
        self.connectNetworkElementsAction.setEnabled(False)
        self.connectNetworkElementsAction.setCheckable(True)
        self.connectNetworkElementsAction.triggered.connect(self.connectNetworkElements)

        self.refreshNetworkTopologyAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/refresh-network.svg")),
            "Refresh network topology",
            self.iface.mainWindow(),
        )
        self.refreshNetworkTopologyAction.setWhatsThis(self.tr("Refresh network topology"))
        self.refreshNetworkTopologyAction.setEnabled(False)
        self.refreshNetworkTopologyAction.setCheckable(False)
        self.refreshNetworkTopologyAction.triggered.connect(
            self.refreshNetworkTopologyActionClicked
        )

        self.updateSymbologyAction = QAction(self.tr("Update symbology"), self.iface.mainWindow())
        self.updateSymbologyAction.triggered.connect(self.updateSymbology)

        self.validityCheckAction = QAction(self.tr("Validity check"), self.iface.mainWindow())
        self.validityCheckAction.triggered.connect(self.tww_validity_check_action)

        self.enableSymbologyTriggersAction = QAction(
            self.tr("Enable symbology triggers"), self.iface.mainWindow()
        )
        self.enableSymbologyTriggersAction.triggered.connect(self.enable_symbology_triggers)

        self.disableSymbologyTriggersAction = QAction(
            self.tr("Disable symbology triggers"), self.iface.mainWindow()
        )
        self.disableSymbologyTriggersAction.triggered.connect(self.disable_symbology_triggers)

        self.settingsAction = QAction(
            QIcon(QgsApplication.getThemeIcon("/mActionOptions.svg")),
            self.tr("Settings"),
            self.iface.mainWindow(),
        )
        self.settingsAction.triggered.connect(self.showSettings)

        self.aboutAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/teksi-abwasser-logo.svg")),
            self.tr("About"),
            self.iface.mainWindow(),
        )
        self.aboutAction.triggered.connect(self.about)

        self.importAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/interlis_import.svg")),
            self.tr("Import from interlis"),
            self.iface.mainWindow(),
        )
        self.importAction.setWhatsThis(self.tr("Import from INTERLIS"))
        self.importAction.setEnabled(False)
        self.importAction.setCheckable(False)
        self.importAction.triggered.connect(self.actionImportClicked)

        self.exportAction = QAction(
            QIcon(os.path.join(plugin_root_path(), "icons/interlis_export.svg")),
            self.tr("Export to interlis"),
            self.iface.mainWindow(),
        )
        self.exportAction.setWhatsThis(self.tr("Export to INTERLIS"))
        self.exportAction.setEnabled(False)
        self.exportAction.setCheckable(False)
        self.exportAction.triggered.connect(self.actionExportClicked)

        # Add toolbar button and menu item
        self.toolbar = QToolBar(self.tr("TEKSI Wastewater"))
        self.toolbar.setObjectName(self.toolbar.windowTitle())
        self.toolbar.addAction(self.profileAction)
        self.toolbar.addAction(self.upstreamAction)
        self.toolbar.addAction(self.downstreamAction)
        self.toolbar.addAction(self.wizardAction)
        self.toolbar.addAction(self.refreshNetworkTopologyAction)
        self.toolbar.addAction(self.connectNetworkElementsAction)

        self.main_menu_name = "TEKSI &Wastewater"
        self.iface.addPluginToMenu(self.main_menu_name, self.profileAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.updateSymbologyAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.validityCheckAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.enableSymbologyTriggersAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.disableSymbologyTriggersAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.settingsAction)
        self.iface.addPluginToMenu(self.main_menu_name, self.aboutAction)

        self._get_main_menu_action().setIcon(
            QIcon(os.path.join(plugin_root_path(), "icons/teksi-abwasser-logo.svg")),
        )

        self.update_admin_mode()

        self.iface.addToolBar(self.toolbar)

        # Local array of buttons to enable / disable based on context
        self.toolbarButtons.append(self.profileAction)
        self.toolbarButtons.append(self.upstreamAction)
        self.toolbarButtons.append(self.downstreamAction)
        self.toolbarButtons.append(self.wizardAction)
        self.toolbarButtons.append(self.refreshNetworkTopologyAction)
        self.toolbarButtons.append(self.importAction)
        self.toolbarButtons.append(self.exportAction)

        self.network_layer_notifier.layersAvailable.connect(self.onNetworkLayersAvailable)
        self.network_layer_notifier.layersUnavailable.connect(self.onNetworkLayersUnavailable)

        self.vw_tww_layer_notifier.layersAvailable.connect(self.onTwwLayersAvailable)
        self.vw_tww_layer_notifier.layersUnavailable.connect(self.onTwwLayersUnavailable)

        # Init the object maintaining the network
        self.network_analyzer = TwwGraphManager()
        self.network_analyzer.message_emitted.connect(self.iface.messageBar().pushMessage)
        #Create the map tool for profile selection
        self.profile_tool = TwwProfileMapTool(
           self.iface, self.profileAction, self.network_analyzer
        )
        self.profile_tool.profileChanged.connect(self.onProfileChanged)

        self.upstream_tree_tool = TwwTreeMapTool(
            self.iface, self.upstreamAction, self.network_analyzer
        )
        self.upstream_tree_tool.setDirection("upstream")
        self.upstream_tree_tool.treeChanged.connect(self.onTreeChanged)
        self.downstream_tree_tool = TwwTreeMapTool(
            self.iface, self.downstreamAction, self.network_analyzer
        )
        self.downstream_tree_tool.setDirection("downstream")
        self.downstream_tree_tool.treeChanged.connect(self.onTreeChanged)

        self.maptool_connect_networkelements = TwwMapToolConnectNetworkElements(
            self.iface, self.connectNetworkElementsAction
        )

        self.processing_provider = TwwProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.processing_provider)

        self.network_layer_notifier.layersAdded([])

    def tww_validity_check_startup(self):
        messages = []
        try:
            messages = DatabaseUtils.get_validity_check_issues()

        except Exception as exception:
            messages.append(self.tr(f"Could not check database validity: {exception}"))

        for message in messages:
            self.iface.messageBar().pushMessage(
                "Warning",
                message,
                level=Qgis.Warning,
            )

    def tww_validity_check_action(self):
        messages = []
        try:
            messages = DatabaseUtils.get_validity_check_issues()

        except Exception as exception:
            messages.append(self.tr(f"Could not check database validity: {exception}"))

        if len(messages) == 0:
            QMessageBox.information(
                self.iface.mainWindow(),
                self.validityCheckAction.text(),
                self.tr("There are no database validity issues."),
            )
            return

        messagesText = "\n".join(messages)
        QMessageBox.critical(
            self.iface.mainWindow(),
            self.validityCheckAction.text(),
            self.tr(f"Database has following validity issues:\n\n{messagesText}"),
        )

    def enable_symbology_triggers(self):
        try:
            DatabaseUtils.enable_symbology_triggers()
            QMessageBox.information(
                self.iface.mainWindow(),
                self.enableSymbologyTriggersAction.text(),
                self.tr("Symbology triggers have been successfully enabled"),
            )

        except Exception as exception:
            QMessageBox.critical(
                self.iface.mainWindow(),
                self.enableSymbologyTriggersAction.text(),
                self.tr(f"Symbology triggers cannot be enabled:\n\n{exception}"),
            )

    def disable_symbology_triggers(self):
        try:
            DatabaseUtils.disable_symbology_triggers()
            QMessageBox.information(
                self.iface.mainWindow(),
                self.disableSymbologyTriggersAction.text(),
                self.tr("Symbology triggers have been successfully disabled"),
            )

        except Exception as exception:
            QMessageBox.critical(
                self.iface.mainWindow(),
                self.disableSymbologyTriggersAction.text(),
                self.tr(f"Symbology triggers cannot be disabled:\n\n{exception}"),
            )

    def unload(self):
        """
        Called when unloading
        """
        self.toolbar.removeAction(self.profileAction)
        self.toolbar.removeAction(self.upstreamAction)
        self.toolbar.removeAction(self.downstreamAction)
        self.toolbar.removeAction(self.wizardAction)
        self.toolbar.removeAction(self.refreshNetworkTopologyAction)
        self.toolbar.removeAction(self.connectNetworkElementsAction)

        if self.importAction in self.toolbar.actions():
            self.toolbar.removeAction(self.importAction)
        if self.exportAction in self.toolbar.actions():
            self.toolbar.removeAction(self.exportAction)

        self.toolbar.deleteLater()

        self.iface.removePluginMenu(self.main_menu_name, self.profileAction)
        self.iface.removePluginMenu(self.main_menu_name, self.updateSymbologyAction)
        self.iface.removePluginMenu(self.main_menu_name, self.validityCheckAction)
        self.iface.removePluginMenu(self.main_menu_name, self.settingsAction)
        self.iface.removePluginMenu(self.main_menu_name, self.aboutAction)
        self.iface.removePluginMenu(self.main_menu_name, self.enableSymbologyTriggersAction)
        self.iface.removePluginMenu(self.main_menu_name, self.disableSymbologyTriggersAction)

        QgsApplication.processingRegistry().removeProvider(self.processing_provider)

    def onNetworkLayersAvailable(self, layers):
        self.connectNetworkElementsAction.setEnabled(True)
        self.network_analyzer.setReachLayer(layers["vw_network_segment"])
        self.network_analyzer.setNodeLayer(layers["vw_network_node"])

    def onNetworkLayersUnavailable(self):
        self.connectNetworkElementsAction.setEnabled(False)

    def onTwwLayersAvailable(self):
        for b in self.toolbarButtons:
            b.setEnabled(True)

        self._configure_database_connection_config_from_tww_layer()
        self.tww_validity_check_startup()

    def onTwwLayersUnavailable(self):
        for b in self.toolbarButtons:
            b.setEnabled(False)


    def profileToolClicked(self):
        """
        Is executed when the profile button is clicked
        """
        
        self.openDock()
        # Set the profile map tool
        self.profile_tool.setActive()

    def profileToolClicked2(self):
        """
        Is executed when the profile button is clicked
        """
        
        action = self.iface.mainWindow().findChild(QAction, 'mActionElevationProfile')
        if action:
            action.trigger()
            
            # Configure elevation profile after it opens
            from qgis.PyQt.QtCore import QTimer
            from qgis.PyQt.QtWidgets import QDockWidget, QToolButton
            from qgis.core import QgsVectorLayerElevationProperties, QgsProject
            
            def configureElevationProfile(retry_count=0):
                max_retries = 5
                main_window = self.iface.mainWindow()
                
                # Find elevation profile dock widget
                dock_widgets = main_window.findChildren(QDockWidget)
                elevation_dock = None
                for dock in dock_widgets:
                    if dock.isVisible():
                        title_lower = dock.windowTitle().lower()
                        if 'elevation' in title_lower or 'profile' in title_lower or 'elevationprofile' in dock.objectName().lower():
                            elevation_dock = dock
                            break
                
                if not elevation_dock and retry_count < max_retries:
                    # Retry after a longer delay
                    QTimer.singleShot(500, lambda: configureElevationProfile(retry_count + 1))
                    return
                
                if not elevation_dock:
                    self.logger.warning("Elevation profile dock widget not found")
                    return
                
                self.logger.debug(f"Found elevation profile dock: {elevation_dock.windowTitle()}")
                
                # 1. Find and trigger capture curve tool
                # Try multiple ways to find the capture curve action
                capture_action = None
                
                # Method 1: Search in main window
                all_actions = main_window.findChildren(QAction)
                for act in all_actions:
                    obj_name = act.objectName().lower()
                    text = act.text().lower()
                    if 'capture' in obj_name and 'curve' in obj_name:
                        capture_action = act
                        self.logger.debug(f"Found capture curve action by object name: {act.objectName()}")
                        break
                    elif 'capture' in text and 'curve' in text:
                        capture_action = act
                        self.logger.debug(f"Found capture curve action by text: {act.text()}")
                        break
                
                # Method 2: Search in dock widget
                if not capture_action:
                    dock_actions = elevation_dock.findChildren(QAction)
                    for act in dock_actions:
                        obj_name = act.objectName().lower()
                        text = act.text().lower()
                        if 'capture' in obj_name and 'curve' in obj_name:
                            capture_action = act
                            self.logger.debug(f"Found capture curve action in dock by object name: {act.objectName()}")
                            break
                        elif 'capture' in text and 'curve' in text:
                            capture_action = act
                            self.logger.debug(f"Found capture curve action in dock by text: {act.text()}")
                            break
                
                # Method 3: Search for tool buttons in dock
                if not capture_action:
                    tool_buttons = elevation_dock.findChildren(QToolButton)
                    for btn in tool_buttons:
                        default_action = btn.defaultAction()
                        if default_action:
                            obj_name = default_action.objectName().lower()
                            text = default_action.text().lower()
                            if 'capture' in obj_name and 'curve' in obj_name:
                                capture_action = default_action
                                self.logger.debug(f"Found capture curve action in tool button: {default_action.objectName()}")
                                break
                            elif 'capture' in text and 'curve' in text:
                                capture_action = default_action
                                self.logger.debug(f"Found capture curve action in tool button by text: {default_action.text()}")
                                break
                
                if capture_action:
                    capture_action.trigger()
                    self.logger.info("Capture curve tool activated")
                else:
                    self.logger.warning("Could not find capture curve action")
                
                # 2. Configure layers in Elevation Profile window
                layer_names = ['vw_cover', 'vw_wastewater_node', 'vw_tww_reach', 'vw_change_points']
                
                # Find QgsElevationProfileCanvas and layer list widget in the elevation profile dock widget
                from qgis.gui import QgsElevationProfileCanvas
                from qgis.core import QgsProject
                from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem
                from qgis.PyQt.QtCore import Qt
                
                elevation_canvas = None
                # Search for QgsElevationProfileCanvas in the dock widget
                canvas_widgets = elevation_dock.findChildren(QgsElevationProfileCanvas)
                if canvas_widgets:
                    elevation_canvas = canvas_widgets[0]
                    self.logger.debug("Found QgsElevationProfileCanvas in elevation profile dock")
                
                # Try to find layer list widget (could be QTreeWidget or QListWidget)
                layer_tree_widget = None
                tree_widgets = elevation_dock.findChildren(QTreeWidget)
                for tree in tree_widgets:
                    # Check if this looks like a layer list (has items or specific object name)
                    if tree.objectName() and ('layer' in tree.objectName().lower() or 'tree' in tree.objectName().lower()):
                        layer_tree_widget = tree
                        self.logger.debug(f"Found layer tree widget: {tree.objectName()}")
                        break
                    elif tree.topLevelItemCount() > 0:
                        # If it has items, it might be the layer list
                        layer_tree_widget = tree
                        self.logger.debug(f"Found tree widget with items: {tree.objectName()}")
                        break
                
                # Collect layers to enable
                layers_to_enable = []
                for layer_name in layer_names:
                    layer = TwwLayerManager.layer(layer_name)
                    if not layer:
                        self.logger.debug(f"Layer {layer_name} not found, skipping")
                        continue
                    layers_to_enable.append(layer)
                    self.logger.debug(f"Found layer: {layer_name} (ID: {layer.id()})")
                
                # Enable layers in the layer list widget
                if layer_tree_widget and layers_to_enable:
                    try:
                        # Helper function to recursively check items
                        def check_items_recursive(item, layers_to_check):
                            matched = False
                            if item:
                                # Try to find layer ID in item data or text
                                item_text = item.text(0) if item.columnCount() > 0 else ""
                                
                                # Check if this item corresponds to one of our layers
                                for layer in layers_to_check:
                                    layer_id = layer.id()
                                    layer_name_check = layer.name()
                                    
                                    # Check if item text contains layer name or ID
                                    if layer_name_check in item_text or layer_id in item_text:
                                        # Set item to checked
                                        item.setCheckState(0, Qt.Checked)
                                        self.logger.info(f"Checked layer {layer_name_check} in elevation profile layer list")
                                        
                                        # Expand parent if it's a tree
                                        parent = item.parent()
                                        if parent:
                                            parent.setExpanded(True)
                                        
                                        matched = True
                                        break
                                    
                                    # Also try checking by data (layer might be stored as data)
                                    for col in range(item.columnCount()):
                                        item_data = item.data(col, Qt.UserRole)
                                        if item_data:
                                            item_data_str = str(item_data)
                                            if layer_id in item_data_str or layer_name_check in item_data_str:
                                                item.setCheckState(0, Qt.Checked)
                                                self.logger.info(f"Checked layer {layer_name_check} in elevation profile layer list (by data)")
                                                
                                                # Expand parent
                                                parent = item.parent()
                                                if parent:
                                                    parent.setExpanded(True)
                                                
                                                matched = True
                                                break
                                
                                # Recursively check children
                                for i in range(item.childCount()):
                                    child = item.child(i)
                                    if check_items_recursive(child, layers_to_check):
                                        # Expand parent if child was matched
                                        item.setExpanded(True)
                                        matched = True
                            
                            return matched
                        
                        # Check top-level items recursively
                        for i in range(layer_tree_widget.topLevelItemCount()):
                            item = layer_tree_widget.topLevelItem(i)
                            check_items_recursive(item, layers_to_enable)
                            
                    except Exception as e:
                        self.logger.warning(f"Failed to enable layers in layer list widget: {e}")
                        import traceback
                        self.logger.debug(traceback.format_exc())
                
                # Set layers to elevation profile canvas if found
                if elevation_canvas and layers_to_enable:
                    try:
                        # Get current layers
                        current_layers = []
                        if hasattr(elevation_canvas, 'layers'):
                            current_layers = list(elevation_canvas.layers()) if callable(elevation_canvas.layers) else list(elevation_canvas.layers)
                        
                        # Add our layers to the canvas
                        if hasattr(elevation_canvas, 'setLayers'):
                            # Combine current layers with new layers (avoid duplicates)
                            all_layers = list(current_layers)
                            for layer in layers_to_enable:
                                if layer not in all_layers:
                                    all_layers.append(layer)
                            elevation_canvas.setLayers(all_layers)
                            self.logger.info(f"Set {len(all_layers)} layers to elevation profile canvas")
                        else:
                            self.logger.warning("QgsElevationProfileCanvas does not have setLayers method")
                    except Exception as e:
                        self.logger.warning(f"Failed to set layers to elevation canvas: {e}")
                        import traceback
                        self.logger.debug(traceback.format_exc())
                
                # Configure elevation properties for each layer
                for layer_name in layer_names:
                    layer = TwwLayerManager.layer(layer_name)
                    if not layer:
                        continue
                    
                    self.logger.debug(f"Configuring elevation properties for layer: {layer_name}")
                    
                    # Get elevation properties
                    elevation_props = layer.elevationProperties()
                    if not elevation_props:
                        self.logger.warning(f"Layer {layer_name} has no elevation properties, skipping")
                        continue
                    
                    # Log elevation properties type and available methods
                    props_type = type(elevation_props).__name__
                    self.logger.debug(f"Layer {layer_name} elevation properties type: {props_type}")
                    
                    # Enable elevation - try different methods
                    try:
                        # Method 1: Try isEnabled/setEnabled
                        if hasattr(elevation_props, 'isEnabled') and hasattr(elevation_props, 'setEnabled'):
                            if not elevation_props.isEnabled():
                                elevation_props.setEnabled(True)
                                self.logger.info(f"Enabled elevation for layer {layer_name} (method)")
                            else:
                                self.logger.debug(f"Elevation already enabled for layer {layer_name}")
                        # Method 2: Try enabled property
                        elif hasattr(elevation_props, 'enabled'):
                            if not elevation_props.enabled:
                                elevation_props.enabled = True
                                self.logger.info(f"Enabled elevation for layer {layer_name} (property)")
                            else:
                                self.logger.debug(f"Elevation already enabled for layer {layer_name}")
                        # Method 3: Try isActive/setActive
                        elif hasattr(elevation_props, 'isActive') and hasattr(elevation_props, 'setActive'):
                            if not elevation_props.isActive():
                                elevation_props.setActive(True)
                                self.logger.info(f"Activated elevation for layer {layer_name}")
                            else:
                                self.logger.debug(f"Elevation already active for layer {layer_name}")
                        else:
                            # Log available attributes for debugging
                            available_attrs = [attr for attr in dir(elevation_props) if not attr.startswith('_') and not callable(getattr(elevation_props, attr, None))]
                            available_methods = [attr for attr in dir(elevation_props) if not attr.startswith('_') and callable(getattr(elevation_props, attr, None))]
                            self.logger.debug(f"Available elevation properties attributes: {available_attrs[:10]}")
                            self.logger.debug(f"Available elevation properties methods: {[m for m in available_methods if 'enable' in m.lower() or 'active' in m.lower()][:10]}")
                            # Try to enable anyway if it's a vector layer elevation properties
                            if isinstance(elevation_props, QgsVectorLayerElevationProperties):
                                # For vector layers, elevation might be enabled by default or need mode setting
                                self.logger.debug(f"Vector layer elevation properties - checking mode")
                    except Exception as e:
                        self.logger.warning(f"Failed to enable elevation for {layer_name}: {e}")
                        import traceback
                        self.logger.debug(traceback.format_exc())
                    
                    # Set clamping to Absolute
                    if isinstance(elevation_props, QgsVectorLayerElevationProperties):
                        try:
                            # Try different methods to set clamping
                            clamping_set = False
                            
                            # First, check current clamping value and available methods
                            current_clamping = None
                            if hasattr(elevation_props, 'clamping'):
                                try:
                                    current_clamping = elevation_props.clamping
                                    self.logger.debug(f"Current clamping value for {layer_name}: {current_clamping}")
                                except:
                                    pass
                            
                            # Method 1: Try setClamping with enum
                            if hasattr(elevation_props, 'setClamping'):
                                try:
                                    if hasattr(QgsVectorLayerElevationProperties, 'Clamping'):
                                        clamping_enum = QgsVectorLayerElevationProperties.Clamping
                                        # Check available enum values
                                        enum_attrs = [attr for attr in dir(clamping_enum) if not attr.startswith('_')]
                                        self.logger.debug(f"Available clamping enum values for {layer_name}: {enum_attrs}")
                                        
                                        # Try different possible enum values
                                        if hasattr(clamping_enum, 'Absolute'):
                                            elevation_props.setClamping(clamping_enum.Absolute)
                                            clamping_set = True
                                            self.logger.info(f"Set clamping to Absolute (enum) for layer {layer_name}")
                                        elif hasattr(clamping_enum, 'AbsoluteClamping'):
                                            elevation_props.setClamping(clamping_enum.AbsoluteClamping)
                                            clamping_set = True
                                            self.logger.info(f"Set clamping to AbsoluteClamping (enum) for layer {layer_name}")
                                        elif 'Absolute' in enum_attrs:
                                            # Try to get the enum value dynamically
                                            abs_value = getattr(clamping_enum, 'Absolute', None)
                                            if abs_value is not None:
                                                elevation_props.setClamping(abs_value)
                                                clamping_set = True
                                                self.logger.info(f"Set clamping to Absolute (dynamic enum) for layer {layer_name}")
                                        else:
                                            # Try numeric value 0 (Absolute is typically 0)
                                            elevation_props.setClamping(0)
                                            clamping_set = True
                                            self.logger.info(f"Set clamping to 0 (Absolute) for layer {layer_name}")
                                except Exception as e1:
                                    self.logger.debug(f"Method 1 (setClamping with enum) failed for {layer_name}: {e1}")
                                    # Try numeric value directly
                                    try:
                                        elevation_props.setClamping(0)
                                        clamping_set = True
                                        self.logger.info(f"Set clamping to 0 (Absolute) for layer {layer_name} (direct numeric)")
                                    except Exception as e2:
                                        self.logger.debug(f"Method 1 (direct numeric) also failed for {layer_name}: {e2}")
                            
                            # Method 2: Try clamping property
                            if not clamping_set and hasattr(elevation_props, 'clamping'):
                                try:
                                    if hasattr(QgsVectorLayerElevationProperties, 'Clamping'):
                                        clamping_enum = QgsVectorLayerElevationProperties.Clamping
                                        if hasattr(clamping_enum, 'Absolute'):
                                            elevation_props.clamping = clamping_enum.Absolute
                                            clamping_set = True
                                            self.logger.info(f"Set clamping property to Absolute for layer {layer_name}")
                                        else:
                                            elevation_props.clamping = 0
                                            clamping_set = True
                                            self.logger.info(f"Set clamping property to 0 (Absolute) for layer {layer_name}")
                                    else:
                                        elevation_props.clamping = 0
                                        clamping_set = True
                                        self.logger.info(f"Set clamping property to 0 (Absolute) for layer {layer_name} (direct)")
                                except Exception as e2:
                                    self.logger.debug(f"Method 2 (clamping property) failed for {layer_name}: {e2}")
                            
                            # Verify clamping was set
                            if clamping_set:
                                try:
                                    if hasattr(elevation_props, 'clamping'):
                                        final_clamping = elevation_props.clamping
                                        self.logger.debug(f"Verified clamping value for {layer_name}: {final_clamping}")
                                except:
                                    pass
                            else:
                                # Log available methods for debugging
                                available_methods = [attr for attr in dir(elevation_props) if not attr.startswith('_') and 'clamp' in attr.lower()]
                                self.logger.warning(f"Could not set clamping for layer {layer_name} - available clamping methods: {available_methods}")
                                
                        except Exception as e:
                            self.logger.warning(f"Failed to configure clamping for {layer_name}: {e}")
                            import traceback
                            self.logger.debug(traceback.format_exc())
                    
                    # Trigger layer repaint and commit changes
                    try:
                        layer.triggerRepaint()
                        # Also try to commit style changes
                        if hasattr(layer, 'styleManager'):
                            layer.styleManager().saveCurrentStyle()
                    except Exception as e:
                        self.logger.debug(f"Failed to trigger repaint for {layer_name}: {e}")
            
            # Use QTimer with longer delay and retry mechanism
            QTimer.singleShot(500, lambda: configureElevationProfile(0))

    def upstreamToolClicked(self):
        """
        Is executed when the user clicks the upstream search tool
        """
        self.openDock()
        self.upstream_tree_tool.setActive()

    def downstreamToolClicked(self):
        """
        Is executed when the user clicks the downstream search tool
        """
        self.openDock()
        self.downstream_tree_tool.setActive()

    def refreshNetworkTopologyActionClicked(self):
        """
        Is executed when the user clicks the refreshNetworkTopologyAction tool
        """
        self.network_analyzer.refresh()

    def wizard(self):
        """"""
        if not self.wizarddock:
            self.wizarddock = TwwWizard(self.iface.mainWindow(), self.iface)
        self.logger.debug("Opening Wizard")
        self.iface.addDockWidget(Qt.LeftDockWidgetArea, self.wizarddock)
        self.wizarddock.show()

    def connectNetworkElements(self, checked):
        self.iface.mapCanvas().setMapTool(self.maptool_connect_networkelements)

    def openDock(self):
        """
        Opens the dock
        """
        if self.profile_dock is None:
            self.logger.debug("Open dock")
            self.profile_dock = TwwProfileDockWidget(
                self.iface.mainWindow(),
                self.iface.mapCanvas(),
                self.iface.addDockWidget,
            )
            self.profile_dock.closed.connect(self.onDockClosed)
            self.profile_dock.showIt()

            self.plotWidget = None
            # Use the new Elevation Profile widget if available
            if TwwElevationProfileWidget is not None:
                print("*** Using NEW TwwElevationProfileWidget (QGIS Elevation Profile Canvas) ***")
                self.plotWidget = TwwElevationProfileWidget(self.profile_dock, self.network_analyzer)
                # Note: The new widget doesn't have mouseOver signals (those were for the old SVG implementation)
                # TODO: Add interactivity features if needed in the future
                self.profile_dock.addPlotWidget(self.plotWidget)
                self.profile_dock.setTree(self.nodes, self.edges)
            elif TwwPlotSVGWidget is not None:
                # Fallback to old widget if new one is not available
                #self.logger.info("Using OLD TwwPlotSVGWidget (QtWebKit fallback)")
                print("*** Using OLD TwwPlotSVGWidget (QtWebKit fallback) ***")
                self.plotWidget = TwwPlotSVGWidget(self.profile_dock, self.network_analyzer)
                self.plotWidget.specialStructureMouseOver.connect(self.highlightProfileElement)
                self.plotWidget.specialStructureMouseOut.connect(self.unhighlightProfileElement)
                self.plotWidget.reachMouseOver.connect(self.highlightProfileElement)
                self.plotWidget.reachMouseOut.connect(self.unhighlightProfileElement)
                self.profile_dock.addPlotWidget(self.plotWidget)
                self.profile_dock.setTree(self.nodes, self.edges)

    def onDockClosed(self):  # used when Dock dialog is closed
        """
        Gets called when the dock is closed
        All the cleanup of the dock has to be done here
        """
        self.profile_dock = None

    def onProfileChanged(self, profile):
        """
        The profile changed: update the plot
        @param profile: The profile to plot
        """
        self.profile = profile.copy()

        if self.plotWidget:
            # Only call setProfile if the widget has this method (old SVG widget)
            if hasattr(self.plotWidget, 'setProfile'):
                self.plotWidget.setProfile(profile)
            # For new Elevation Profile widget, convert TwwProfile to QgsGeometry
            elif hasattr(self.plotWidget, 'setProfileCurve'):
                from qgis.core import QgsGeometry
                
                self.logger.debug(f"onProfileChanged: Received new profile with {len(profile.getElements())} elements")
                print(f"✓ onProfileChanged: Received new profile with {len(profile.getElements())} elements")
                
                # Get geometry directly from profile_tool's pathPolyline
                # This is already in the correct order (built in appendProfile)
                if hasattr(self, 'profile_tool') and hasattr(self.profile_tool, 'pathPolyline'):
                    path_polyline = self.profile_tool.pathPolyline
                    if path_polyline and len(path_polyline) > 0:
                        self.logger.debug(f"onProfileChanged: Using profile_tool.pathPolyline with {len(path_polyline)} points")
                        print(f"✓ Using profile_tool.pathPolyline with {len(path_polyline)} points")
                        print(f"  First point: {path_polyline[0]}, Last point: {path_polyline[-1]}")
                        
                        profile_geometry = QgsGeometry.fromPolylineXY(path_polyline)
                        if profile_geometry and not profile_geometry.isEmpty():
                            self.logger.debug(f"onProfileChanged: Calling setProfileCurve with geometry")
                            print(f"✓ Calling setProfileCurve with geometry")
                            self.plotWidget.setProfileCurve(profile_geometry)
                        else:
                            self.logger.warning("onProfileChanged: Geometry is empty or invalid")
                            print(f"✗ Geometry is empty or invalid")
                    else:
                        self.logger.warning("onProfileChanged: pathPolyline is empty")
                        print(f"⚠ profile_tool.pathPolyline is empty, trying to build from profile elements")
                        # Fallback: build from profile elements
                        self._buildProfileFromElements(profile)
                else:
                    self.logger.warning("onProfileChanged: profile_tool or pathPolyline not available")
                    print(f"⚠ profile_tool.pathPolyline not available, trying to build from profile elements")
                    # Fallback: build from profile elements
                    self._buildProfileFromElements(profile)
    
    def _buildProfileFromElements(self, profile):
        """
        Fallback method to build profile geometry from profile elements.
        """
        from qgis.core import QgsGeometry, QgsPointXY
        
        reach_elements = [
            elem for elem in profile.getElements() 
            if hasattr(elem, 'type') and elem.type == "reach" and hasattr(elem, 'detail_geometry') and elem.detail_geometry
        ]
        
        if reach_elements:
            self.logger.debug(f"_buildProfileFromElements: Found {len(reach_elements)} reach elements")
            print(f"  Found {len(reach_elements)} reach elements")
            
            # Sort by start offset
            def get_start_offset(elem):
                if hasattr(elem, 'reachPoints') and elem.reachPoints:
                    offsets = [p.get('offset', 0) for p in elem.reachPoints.values() if 'offset' in p]
                    return min(offsets) if offsets else 0
                return 0
            
            reach_elements_sorted = sorted(reach_elements, key=get_start_offset)
            
            points = []
            for elem in reach_elements_sorted:
                if elem.detail_geometry:
                    geom_points = elem.detail_geometry.asPolyline()
                    if points:
                        if points[-1] == geom_points[0]:
                            points.extend(geom_points[1:])
                        else:
                            points.extend(geom_points)
                    else:
                        points.extend(geom_points)
            
            if points:
                self.logger.debug(f"_buildProfileFromElements: Built polyline with {len(points)} points")
                print(f"  Built polyline with {len(points)} points")
                profile_geometry = QgsGeometry.fromPolylineXY(points)
                self.plotWidget.setProfileCurve(profile_geometry)
            else:
                self.logger.warning("_buildProfileFromElements: No points extracted from reach elements")
                print(f"✗ No points extracted from reach elements")
        else:
            self.logger.warning("_buildProfileFromElements: No reach elements found in profile")
            print(f"✗ No reach elements found in profile")

    def onTreeChanged(self, nodes, edges):
        if self.profile_dock:
            self.profile_dock.setTree(nodes, edges)
        self.nodes = nodes
        self.edges = edges

    def highlightProfileElement(self, obj_id):
        if self.profile is not None:
            self.profile.highlight(str(obj_id))

    def unhighlightProfileElement(self):
        if self.profile is not None:
            self.profile.highlight(None)

    def updateSymbology(self):
        try:
            with OverrideCursor(Qt.WaitCursor):
                DatabaseUtils.update_symbology()
            QMessageBox.information(
                self.iface.mainWindow(),
                self.updateSymbologyAction.text(),
                self.tr("Symbology has been successfully updated"),
            )

        except Exception as exception:
            QMessageBox.critical(
                self.iface.mainWindow(),
                self.updateSymbologyAction.text(),
                self.tr(f"Symbology update failed:\n\n{exception}"),
            )

    def showSettings(self):
        settings_dlg = TwwSettingsDialog(self.iface.mainWindow())
        settings_dlg.exec_()

        self.update_admin_mode()

    def about(self):
        from .gui.about_dialog import AboutDialog

        AboutDialog(self.iface.mainWindow()).exec_()

    def actionExportClicked(self):
        if self.interlisImporterExporter is None:
            try:
                # We only import now to avoid useless exception if dependencies aren't met
                from .interlis.gui.interlis_importer_exporter_gui import (
                    InterlisImporterExporterGui,
                )

                self.interlisImporterExporter = InterlisImporterExporterGui()

            except ImportError as e:
                self.iface.messageBar().pushMessage(
                    "Error",
                    "Could not load Interlis exporter due to unmet dependencies. See logs for more details.",
                    level=Qgis.Critical,
                )
                self.logger.error(str(e))
                return

            except JavaNotFoundError as e:
                self.iface.messageBar().pushMessage(
                    "Error",
                    "Could not load Interlis exporter due to missing Java. See logs for more details.",
                    level=Qgis.Critical,
                )
                self.logger.error(str(e))
                return

        try:
            self.interlisImporterExporter.check_dependencies()
        except Exception as exception:
            self.iface.messageBar().pushMessage(
                "Error",
                f"Could not load start the Interlis exporter due to unmet dependencies: {exception}.",
                level=Qgis.Critical,
            )
            self.logger.error(str(exception))
            return

        self.interlisImporterExporter.action_export()

    def actionImportClicked(self):
        if self.interlisImporterExporter is None:
            try:
                # We only import now to avoid useless exception if dependencies aren't met
                from .interlis.gui.interlis_importer_exporter_gui import (
                    InterlisImporterExporterGui,
                )

                self.interlisImporterExporter = InterlisImporterExporterGui()
            except ImportError as e:
                self.iface.messageBar().pushMessage(
                    "Error",
                    "Could not load Interlis importer due to unmet dependencies. See logs for more details.",
                    level=Qgis.Critical,
                )
                self.logger.error(str(e))
                return

            except JavaNotFoundError as e:
                self.iface.messageBar().pushMessage(
                    "Error",
                    "Could not load Interlis importer due to missing Java. See logs for more details.",
                    level=Qgis.Critical,
                )
                self.logger.error(str(e))
                return

        try:
            self.interlisImporterExporter.check_dependencies()
        except Exception as exception:
            self.iface.messageBar().pushMessage(
                "Error",
                f"Could not load start the Interlis importer due to unmet dependencies: {exception}.",
                level=Qgis.Critical,
            )
            self.logger.error(str(exception))
            return

        self.interlisImporterExporter.action_import()

    def _configure_database_connection_config_from_tww_layer(self) -> dict:
        """Configures tww2ili using the currently loaded TWW project layer"""

        pg_layer = TwwLayerManager.layer("vw_tww_wastewater_structure")
        if not pg_layer:
            self.iface.messageBar().pushMessage(
                "Error",
                "Could not determine the Postgres connection information. Make sure the TWW project is loaded.",
                level=Qgis.Critical,
            )

        self.logger.debug(
            f"dataprovider of vw_tww_wastewater_structure: {pg_layer.dataProvider().uri()}"
        )
        DatabaseUtils.databaseConfig.PGSERVICE = pg_layer.dataProvider().uri().service()
        DatabaseUtils.databaseConfig.PGHOST = pg_layer.dataProvider().uri().host()
        DatabaseUtils.databaseConfig.PGPORT = pg_layer.dataProvider().uri().port()
        DatabaseUtils.databaseConfig.PGDATABASE = pg_layer.dataProvider().uri().database()
        DatabaseUtils.databaseConfig.PGUSER = pg_layer.dataProvider().uri().username()
        DatabaseUtils.databaseConfig.PGPASS = pg_layer.dataProvider().uri().password()

    def _get_main_menu_action(self):
        actions = self.iface.pluginMenu().actions()
        result_actions = [action for action in actions if action.text() == self.main_menu_name]

        # OSX does not support & in the menu title
        if not result_actions:
            result_actions = [
                action
                for action in actions
                if action.text() == self.main_menu_name.replace("&", "")
            ]

        return result_actions[0]

    def update_admin_mode(self):

        admin_mode = QSettings().value("/TWW/AdminMode", False)
        # seems QGIS loads True as "true" on restart ?!
        if admin_mode and admin_mode != "false":
            admin_mode = True
            self.toolbar.addAction(self.importAction)
            self.toolbar.addAction(self.exportAction)
        else:
            self.toolbar.removeAction(self.importAction)
            self.toolbar.removeAction(self.exportAction)
            admin_mode = False

        self.enableSymbologyTriggersAction.setEnabled(admin_mode)
        self.disableSymbologyTriggersAction.setEnabled(admin_mode)
