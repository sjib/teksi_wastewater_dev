# -----------------------------------------------------------
#
# Profile
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

from qgis.PyQt.QtCore import QSettings, Qt, QUrl, pyqtSignal, pyqtSlot
from qgis.PyQt.QtPrintSupport import QPrinter, QPrintPreviewDialog
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget

from ..tools.twwnetwork import TwwGraphManager
from ..utils.translation import TwwJsTranslator
from ..utils.ui import plugin_root_path

# Try to import QtWebEngine (preferred), fallback to QtWebKit if unavailable
WEBENGINE_AVAILABLE = False
WEBKIT_AVAILABLE = False

logger = logging.getLogger(__name__)

try:
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineSettings
    from qgis.PyQt.QtWebChannel import QWebChannel
    WEBENGINE_AVAILABLE = True
    logger.info("QtWebEngine is available")
except ImportError as e:
    logger.debug(f"QtWebEngine not available: {e}")
    try:
        # Fallback to QtWebKit if available
        from qgis.PyQt.QtWebKit import QWebSettings
        from qgis.PyQt.QtWebKitWidgets import QWebPage, QWebView
        WEBKIT_AVAILABLE = True
        logger.info("QtWebKit is available (fallback)")
    except ImportError as e2:
        logger.warning(f"Neither QtWebEngine nor QtWebKit is available. QtWebEngine error: {e}, QtWebKit error: {e2}")
        pass


class TwwWebPage:
    """Web page class supporting both QtWebEngine and QtWebKit"""
    logger = logging.getLogger(__name__)

    def __init__(self, parent):
        if WEBENGINE_AVAILABLE:
            self.page = QWebEnginePage(parent)
            # QtWebEngine uses javascriptConsoleMessage signal
            self.page.javaScriptConsoleMessage.connect(self.onJavaScriptConsoleMessage)
        elif WEBKIT_AVAILABLE:
            self.page = QWebPage(parent)
            # QtWebKit uses javaScriptConsoleMessage method
            # This method will be overridden in a subclass
        else:
            raise ImportError("Neither QtWebEngine nor QtWebKit is available")
    
    def __getattr__(self, name):
        # Proxy all attribute access to the actual page object
        return getattr(self.page, name)
    
    def onJavaScriptConsoleMessage(self, level, msg, line, source):
        """Handle JavaScript console messages for QtWebEngine"""
        self.logger.debug(f"{source} line {line}: {msg}")


class TwwWebKitPage(QWebPage):
    """Web page class specifically for QtWebKit"""
    logger = logging.getLogger(__name__)

    def javaScriptConsoleMessage(self, msg, line, source):
        """Handle JavaScript console messages for QtWebKit"""
        self.logger.debug(f"{source} line {line}: {msg}")


class TwwPlotSVGWidget(QWidget):
    webView = None
    webPage = None
    frame = None
    profile = None
    verticalExaggeration = 10
    jsTranslator = TwwJsTranslator()
    webChannel = None

    # Signals emitted triggered by javascript actions
    reachClicked = pyqtSignal([str], name="reachClicked")
    reachMouseOver = pyqtSignal([str], name="reachMouseOver")
    reachMouseOut = pyqtSignal([str], name="reachMouseOut")
    reachPointClicked = pyqtSignal([str, str], name="reachPointClicked")
    reachPointMouseOver = pyqtSignal([str, str], name="reachPointMouseOver")
    reachPointMouseOut = pyqtSignal([str, str], name="reachPointMouseOut")
    specialStructureClicked = pyqtSignal([str], name="specialStructureClicked")
    specialStructureMouseOver = pyqtSignal([str], name="specialStructureMouseOver")
    specialStructureMouseOut = pyqtSignal([str], name="specialStructureMouseOut")

    # Signals emitted for javascript
    profileChanged = pyqtSignal([str], name="profileChanged")
    verticalExaggerationChanged = pyqtSignal([int], name="verticalExaggerationChanged")

    def __init__(self, parent, network_analyzer: TwwGraphManager, url: str = None):
        QWidget.__init__(self, parent)
        
        logger = logging.getLogger(__name__)
        logger.info(f"Initializing TwwPlotSVGWidget - WEBENGINE_AVAILABLE: {WEBENGINE_AVAILABLE}, WEBKIT_AVAILABLE: {WEBKIT_AVAILABLE}")

        if not WEBENGINE_AVAILABLE and not WEBKIT_AVAILABLE:
            error_msg = "Neither QtWebEngine nor QtWebKit is available"
            logger.error(error_msg)
            raise ImportError(error_msg)

        # Create WebView
        if WEBENGINE_AVAILABLE:
            logger.info("Using QtWebEngine")
            self.webView = QWebEngineView()
            self.webPage = TwwWebPage(self.webView)
            self.webView.setPage(self.webPage.page)
            # Set up WebChannel for JavaScript communication
            self.webChannel = QWebChannel(self.webView.page())
            self.webView.page().setWebChannel(self.webChannel)
        else:
            logger.info("Using QtWebKit")
            self.webView = QWebView()
            self.webPage = TwwWebKitPage(self.webView)
            self.webView.setPage(self.webPage)

        self.networkAnalyzer = network_analyzer

        settings = QSettings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        if url is None:
            # Starting with QGIS 3.4, QWebView requires paths with / even on windows.
            default_url = plugin_root_path().replace("\\", "/") + "/svgprofile/index.html"
            url = settings.value("/TWW/SvgProfilePath", default_url)
            url = "file:///" + url
        
        logger.info(f"Loading profile HTML from: {url}")

        developer_mode = settings.value("/TWW/DeveloperMode", False, type=bool)

        if WEBENGINE_AVAILABLE:
            if developer_mode is True:
                self.webView.page().settings().setAttribute(QWebEngineSettings.WebAttribute.DeveloperExtrasEnabled, True)
            else:
                self.webView.setContextMenuPolicy(Qt.NoContextMenu)
            
            self.webView.load(QUrl(url))
            # Wait for page to finish loading before initializing JavaScript
            self.webView.page().loadFinished.connect(self.onPageLoadFinished)
            logger.info("QtWebEngine: Connected loadFinished signal")
        else:
            if developer_mode is True:
                self.webView.page().settings().setAttribute(QWebSettings.DeveloperExtrasEnabled, True)
            else:
                self.webView.setContextMenuPolicy(Qt.NoContextMenu)
            
            self.webView.load(QUrl(url))
            self.frame = self.webView.page().mainFrame()
            self.frame.javaScriptWindowObjectCleared.connect(self.initJs)
            logger.info("QtWebKit: Connected javaScriptWindowObjectCleared signal")

        # Set minimum size for WebView
        self.webView.setMinimumSize(400, 300)
        self.setMinimumSize(400, 300)
        
        layout.addWidget(self.webView)
        logger.info("WebView added to layout")
        
        # Ensure the widget is visible
        self.webView.show()
        self.show()

    def setProfile(self, profile):
        self.profile = profile
        # Forward to javascript
        self.profileChanged.emit(profile.asJson())

    def onPageLoadFinished(self, success):
        """Called when page loading is finished, used for QtWebEngine"""
        logger = logging.getLogger(__name__)
        logger.info(f"Page load finished, success: {success}")
        if success:
            # Check if page loaded correctly
            self.webView.page().runJavaScript("document.readyState", lambda result: logger.info(f"Page readyState: {result}"))
            self.webView.page().runJavaScript("typeof profileProxy", lambda result: logger.info(f"profileProxy type: {result}"))
            self.initJs()
        else:
            logger.warning("Page load failed")
            # Try to get error information
            if WEBENGINE_AVAILABLE:
                self.webView.page().runJavaScript("document.title", lambda result: logger.warning(f"Page title after failed load: {result}"))

    def initJs(self):
        """Initialize JavaScript bridge"""
        logger = logging.getLogger(__name__)
        logger.info("Initializing JavaScript bridge")
        if WEBENGINE_AVAILABLE:
            # Register objects using WebChannel
            # The WebChannel initialization code in the HTML file will automatically set these objects on window
            self.webChannel.registerObject("profileProxy", self)
            self.webChannel.registerObject("i18n", self.jsTranslator)
            logger.info("Registered objects with WebChannel: profileProxy, i18n")
        else:
            if self.frame:
                self.frame.addToJavaScriptWindowObject("profileProxy", self)
                self.frame.addToJavaScriptWindowObject("i18n", self.jsTranslator)
                logger.info("Added objects to JavaScript window: profileProxy, i18n")
            else:
                logger.warning("Frame is None, cannot add JavaScript objects")

    def changeVerticalExaggeration(self, val):
        self.verticalExaggeration = val
        self.verticalExaggerationChanged.emit(val)

    def printProfile(self):
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setPaperSize(QPrinter.A4)
        printer.setOrientation(QPrinter.Landscape)

        printpreviewdlg = QPrintPreviewDialog()
        printpreviewdlg.paintRequested.connect(self.printRequested)

        printpreviewdlg.exec_()

    @pyqtSlot(QPrinter)
    def printRequested(self, printer):
        """Handle print request"""
        if WEBENGINE_AVAILABLE:
            self.webView.page().print(printer, lambda success: None)
        else:
            self.webView.print_(printer)

    @pyqtSlot(str)
    def onReachClicked(self, obj_id):
        self.reachClicked.emit(obj_id)

    @pyqtSlot(str)
    def onReachMouseOver(self, obj_id):
        self.reachMouseOver.emit(obj_id)

    @pyqtSlot(str)
    def onReachMouseOut(self, obj_id):
        self.reachMouseOut.emit(obj_id)

    @pyqtSlot(str, str)
    def onReachPointClicked(self, obj_id, reach_obj_id):
        self.reachPointClicked.emit(obj_id, reach_obj_id)

    @pyqtSlot(str, str)
    def onReachPointMouseOver(self, obj_id, reach_obj_id):
        self.reachPointMouseOver.emit(obj_id, reach_obj_id)

    @pyqtSlot(str, str)
    def onReachPointMouseOut(self, obj_id, reach_obj_id):
        self.reachPointMouseOut.emit(obj_id, reach_obj_id)

    @pyqtSlot(str)
    def onSpecialStructureClicked(self, obj_id):
        self.specialStructureClicked.emit(obj_id)

    @pyqtSlot(str)
    def onSpecialStructureMouseOver(self, obj_id):
        self.specialStructureMouseOver.emit(obj_id)

    @pyqtSlot(str)
    def onSpecialStructureMouseOut(self, obj_id):
        self.specialStructureMouseOut.emit(obj_id)

    # Is called from the webView when it's been reloaded and wants to have the
    # profile information resent
    @pyqtSlot()
    def updateProfile(self):
        if self.profile:
            self.profileChanged.emit(self.profile.asJson())
            self.verticalExaggerationChanged.emit(self.verticalExaggeration)
