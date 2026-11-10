# ============================================================
# File: ui/core/qt.py
# GridForge V2 — Qt Compatibility / Abstraction Layer
# Author: Subhendu Mishra
# ============================================================

"""Single Qt import boundary for the GridForge UI subsystem."""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QEvent, QEventLoop, QLineF, QModelIndex, QObject, QPoint, QPointF, QRectF, QSize, QSizeF, QSettings, QTimer, Qt, Property, Signal, Slot
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QDoubleValidator, QFont, QIcon, QImage, QIntValidator, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout, QFrame, QSplashScreen, QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QLayout, QFileDialog, QMainWindow, QMenu, QMenuBar, QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget, QStatusBar, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

__all__ = [
    "QAbstractItemModel", "QEvent", "QEventLoop", "QLineF", "QModelIndex", "QObject", "QPoint", "QPointF", "QRectF", "QSize", "QSizeF", "QSettings", "QTimer", "Qt", "Property", "Signal", "Slot",
    "QAction", "QActionGroup", "QBrush", "QColor", "QDoubleValidator", "QFont", "QIntValidator", "QIcon", "QImage", "QPainter", "QPainterPath", "QPainterPathStroker", "QPen", "QPixmap", "QTransform",
    "QChart", "QChartView", "QLineSeries", "QValueAxis",
    "QApplication", "QCheckBox", "QComboBox", "QDockWidget", "QDoubleSpinBox", "QFormLayout", "QFrame", "QFileDialog", "QSplashScreen", "QGraphicsEllipseItem", "QGraphicsItem", "QGraphicsLineItem", "QGraphicsObject", "QGraphicsPathItem", "QGraphicsRectItem", "QGraphicsScene", "QGraphicsTextItem", "QGraphicsView", "QGroupBox", "QHBoxLayout", "QLabel", "QListWidget", "QLayout", "QMainWindow", "QLineEdit", "QMenu", "QMenuBar", "QMessageBox", "QPushButton", "QScrollArea", "QSplitter", "QStackedWidget", "QStatusBar", "QTabBar", "QTabWidget", "QTableWidget", "QTableWidgetItem", "QToolBar", "QToolButton", "QTreeWidget", "QTreeWidgetItem", "QVBoxLayout", "QWidget",
]
