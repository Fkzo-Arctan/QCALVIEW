# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""QCALVIEW compatibility layer for QGIS 3.44/Qt5 and QGIS 4.x/Qt6.

Do not import version-specific enum spellings elsewhere in the plugin.
"""

from qgis.PyQt.QtWidgets import QAbstractItemView
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QColorDialog
from qgis.PyQt.QtWidgets import QDialog
from qgis.PyQt.QtWidgets import QDialogButtonBox
from qgis.PyQt.QtWidgets import QDockWidget
from qgis.PyQt.QtCore import QEvent, QMetaType
from qgis.PyQt.QtWidgets import QFrame
from qgis.PyQt.QtWidgets import QGraphicsView
from qgis.PyQt.QtWidgets import QHeaderView
from qgis.PyQt.QtGui import QImage
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QSizePolicy
from qgis.core import Qgis
from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsVertexMarker
from qgis.core import QgsWkbTypes
from qgis.PyQt.QtCore import Qt

try:
    from qgis.PyQt.QtGui import QAction, QShortcut
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction, QShortcut

def _enum(root, scope_name, member_name):
    """Resolve a scoped Qt6/QGIS4 enum, falling back to its Qt5/QGIS3 alias."""
    scope = getattr(root, scope_name, None)
    if scope is not None:
        value = getattr(scope, member_name, None)
        if value is not None:
            return value
    value = getattr(root, member_name, None)
    if value is not None:
        return value
    raise AttributeError(f"Unable to resolve {root!r}.{scope_name}.{member_name}")

class _CompatValues:
    pass

QC = _CompatValues()
QC.QAbstractItemView_EditTrigger_NoEditTriggers = _enum(QAbstractItemView, 'EditTrigger', 'NoEditTriggers')
QC.QAbstractItemView_SelectionBehavior_SelectRows = _enum(QAbstractItemView, 'SelectionBehavior', 'SelectRows')
QC.QAbstractItemView_SelectionMode_ExtendedSelection = _enum(QAbstractItemView, 'SelectionMode', 'ExtendedSelection')
QC.QAbstractItemView_SelectionMode_SingleSelection = _enum(QAbstractItemView, 'SelectionMode', 'SingleSelection')
QC.QColor_NameFormat_HexArgb = _enum(QColor, 'NameFormat', 'HexArgb')
QC.QColor_NameFormat_HexRgb = _enum(QColor, 'NameFormat', 'HexRgb')
QC.QColorDialog_ColorDialogOption_ShowAlphaChannel = _enum(QColorDialog, 'ColorDialogOption', 'ShowAlphaChannel')
QC.QDialog_DialogCode_Accepted = _enum(QDialog, 'DialogCode', 'Accepted')
QC.QDialogButtonBox_StandardButton_Cancel = _enum(QDialogButtonBox, 'StandardButton', 'Cancel')
QC.QDialogButtonBox_StandardButton_Ok = _enum(QDialogButtonBox, 'StandardButton', 'Ok')
QC.QDialogButtonBox_StandardButton_Reset = _enum(QDialogButtonBox, 'StandardButton', 'Reset')
QC.QDockWidget_DockWidgetFeature_DockWidgetClosable = _enum(QDockWidget, 'DockWidgetFeature', 'DockWidgetClosable')
QC.QDockWidget_DockWidgetFeature_DockWidgetFloatable = _enum(QDockWidget, 'DockWidgetFeature', 'DockWidgetFloatable')
QC.QDockWidget_DockWidgetFeature_DockWidgetMovable = _enum(QDockWidget, 'DockWidgetFeature', 'DockWidgetMovable')
QC.QEvent_Type_MouseButtonPress = _enum(QEvent, 'Type', 'MouseButtonPress')
QC.QEvent_Type_Wheel = _enum(QEvent, 'Type', 'Wheel')
QC.QFrame_Shape_NoFrame = _enum(QFrame, 'Shape', 'NoFrame')
QC.QFrame_Shape_StyledPanel = _enum(QFrame, 'Shape', 'StyledPanel')
QC.QGraphicsView_DragMode_ScrollHandDrag = _enum(QGraphicsView, 'DragMode', 'ScrollHandDrag')
QC.QGraphicsView_ViewportAnchor_AnchorUnderMouse = _enum(QGraphicsView, 'ViewportAnchor', 'AnchorUnderMouse')
QC.QHeaderView_ResizeMode_ResizeToContents = _enum(QHeaderView, 'ResizeMode', 'ResizeToContents')
QC.QHeaderView_ResizeMode_Stretch = _enum(QHeaderView, 'ResizeMode', 'Stretch')
QC.QImage_Format_Format_ARGB32_Premultiplied = _enum(QImage, 'Format', 'Format_ARGB32_Premultiplied')
QC.QImage_Format_Format_RGBA8888 = _enum(QImage, 'Format', 'Format_RGBA8888')
QC.QMessageBox_StandardButton_No = _enum(QMessageBox, 'StandardButton', 'No')
QC.QMessageBox_StandardButton_Yes = _enum(QMessageBox, 'StandardButton', 'Yes')

# QgsField uses QMetaType in QGIS 4; QGIS 3.44 already accepts the same types.
QC.QMetaType_Type_QString = _enum(QMetaType, 'Type', 'QString')
QC.QMetaType_Type_Int = _enum(QMetaType, 'Type', 'Int')
QC.QMetaType_Type_Double = _enum(QMetaType, 'Type', 'Double')
QC.QPainter_CompositionMode_CompositionMode_SourceOver = _enum(QPainter, 'CompositionMode', 'CompositionMode_SourceOver')
QC.QPainter_CompositionMode_CompositionMode_DestinationOver = _enum(QPainter, 'CompositionMode', 'CompositionMode_DestinationOver')
QC.QPainter_RenderHint_Antialiasing = _enum(QPainter, 'RenderHint', 'Antialiasing')
QC.QPainter_RenderHint_SmoothPixmapTransform = _enum(QPainter, 'RenderHint', 'SmoothPixmapTransform')
QC.QPainter_RenderHint_TextAntialiasing = _enum(QPainter, 'RenderHint', 'TextAntialiasing')
QC.QSizePolicy_Policy_Expanding = _enum(QSizePolicy, 'Policy', 'Expanding')
QC.QSizePolicy_Policy_Fixed = _enum(QSizePolicy, 'Policy', 'Fixed')
QC.QSizePolicy_Policy_Ignored = _enum(QSizePolicy, 'Policy', 'Ignored')
QC.QSizePolicy_Policy_Maximum = _enum(QSizePolicy, 'Policy', 'Maximum')
QC.QSizePolicy_Policy_Minimum = _enum(QSizePolicy, 'Policy', 'Minimum')
QC.QSizePolicy_Policy_Preferred = _enum(QSizePolicy, 'Policy', 'Preferred')
QC.Qgis_MessageLevel_Info = _enum(Qgis, 'MessageLevel', 'Info')
QC.Qgis_MessageLevel_Warning = _enum(Qgis, 'MessageLevel', 'Warning')
QC.QgsMapLayerProxyModel_Filter_PointLayer = _enum(QgsMapLayerProxyModel, 'Filter', 'PointLayer')
QC.QgsMapLayerProxyModel_Filter_RasterLayer = _enum(QgsMapLayerProxyModel, 'Filter', 'RasterLayer')
QC.QgsMapLayerProxyModel_Filter_VectorLayer = _enum(QgsMapLayerProxyModel, 'Filter', 'VectorLayer')
QC.QgsVertexMarker_IconType_ICON_CROSS = _enum(QgsVertexMarker, 'IconType', 'ICON_CROSS')
QC.QgsWkbTypes_GeometryType_LineGeometry = _enum(QgsWkbTypes, 'GeometryType', 'LineGeometry')
QC.QgsWkbTypes_GeometryType_NullGeometry = _enum(QgsWkbTypes, 'GeometryType', 'NullGeometry')
QC.QgsWkbTypes_GeometryType_PointGeometry = _enum(QgsWkbTypes, 'GeometryType', 'PointGeometry')
QC.QgsWkbTypes_GeometryType_PolygonGeometry = _enum(QgsWkbTypes, 'GeometryType', 'PolygonGeometry')
QC.QgsWkbTypes_GeometryType_UnknownGeometry = _enum(QgsWkbTypes, 'GeometryType', 'UnknownGeometry')
QC.Qt_AlignmentFlag_AlignCenter = _enum(Qt, 'AlignmentFlag', 'AlignCenter')
QC.Qt_AlignmentFlag_AlignTop = _enum(Qt, 'AlignmentFlag', 'AlignTop')
QC.Qt_ArrowType_DownArrow = _enum(Qt, 'ArrowType', 'DownArrow')
QC.Qt_ArrowType_NoArrow = _enum(Qt, 'ArrowType', 'NoArrow')
QC.Qt_ArrowType_RightArrow = _enum(Qt, 'ArrowType', 'RightArrow')
QC.Qt_AspectRatioMode_IgnoreAspectRatio = _enum(Qt, 'AspectRatioMode', 'IgnoreAspectRatio')
QC.Qt_AspectRatioMode_KeepAspectRatio = _enum(Qt, 'AspectRatioMode', 'KeepAspectRatio')
QC.Qt_BrushStyle_NoBrush = _enum(Qt, 'BrushStyle', 'NoBrush')
QC.Qt_CheckState_Checked = _enum(Qt, 'CheckState', 'Checked')
QC.Qt_CheckState_Unchecked = _enum(Qt, 'CheckState', 'Unchecked')
QC.Qt_DateFormat_ISODate = _enum(Qt, 'DateFormat', 'ISODate')
QC.Qt_DockWidgetArea_AllDockWidgetAreas = _enum(Qt, 'DockWidgetArea', 'AllDockWidgetAreas')
QC.Qt_DockWidgetArea_RightDockWidgetArea = _enum(Qt, 'DockWidgetArea', 'RightDockWidgetArea')
QC.Qt_ItemDataRole_UserRole = _enum(Qt, 'ItemDataRole', 'UserRole')
QC.Qt_ItemFlag_ItemIsEditable = _enum(Qt, 'ItemFlag', 'ItemIsEditable')
QC.Qt_ItemFlag_ItemIsEnabled = _enum(Qt, 'ItemFlag', 'ItemIsEnabled')
QC.Qt_ItemFlag_ItemIsSelectable = _enum(Qt, 'ItemFlag', 'ItemIsSelectable')
QC.Qt_ItemFlag_ItemIsUserCheckable = _enum(Qt, 'ItemFlag', 'ItemIsUserCheckable')
QC.Qt_MouseButton_LeftButton = _enum(Qt, 'MouseButton', 'LeftButton')
QC.Qt_Orientation_Horizontal = _enum(Qt, 'Orientation', 'Horizontal')
QC.Qt_PenCapStyle_RoundCap = _enum(Qt, 'PenCapStyle', 'RoundCap')
QC.Qt_PenJoinStyle_RoundJoin = _enum(Qt, 'PenJoinStyle', 'RoundJoin')
QC.Qt_PenStyle_DashDotDotLine = _enum(Qt, 'PenStyle', 'DashDotDotLine')
QC.Qt_PenStyle_DashDotLine = _enum(Qt, 'PenStyle', 'DashDotLine')
QC.Qt_PenStyle_DashLine = _enum(Qt, 'PenStyle', 'DashLine')
QC.Qt_PenStyle_DotLine = _enum(Qt, 'PenStyle', 'DotLine')
QC.Qt_PenStyle_NoPen = _enum(Qt, 'PenStyle', 'NoPen')
QC.Qt_PenStyle_SolidLine = _enum(Qt, 'PenStyle', 'SolidLine')
QC.Qt_ShortcutContext_ApplicationShortcut = _enum(Qt, 'ShortcutContext', 'ApplicationShortcut')
QC.Qt_TextFormat_RichText = _enum(Qt, 'TextFormat', 'RichText')
QC.Qt_TextInteractionFlag_TextSelectableByMouse = _enum(Qt, 'TextInteractionFlag', 'TextSelectableByMouse')
QC.Qt_ToolButtonStyle_ToolButtonIconOnly = _enum(Qt, 'ToolButtonStyle', 'ToolButtonIconOnly')
QC.Qt_ToolButtonStyle_ToolButtonTextBesideIcon = _enum(Qt, 'ToolButtonStyle', 'ToolButtonTextBesideIcon')
QC.Qt_TransformationMode_FastTransformation = _enum(Qt, 'TransformationMode', 'FastTransformation')
QC.Qt_TransformationMode_SmoothTransformation = _enum(Qt, 'TransformationMode', 'SmoothTransformation')
QC.Qt_WindowModality_WindowModal = _enum(Qt, 'WindowModality', 'WindowModal')
QC.Qt_WindowType_Window = _enum(Qt, 'WindowType', 'Window')
QC.Qt_WindowType_WindowCloseButtonHint = _enum(Qt, 'WindowType', 'WindowCloseButtonHint')
QC.Qt_WindowType_WindowMinMaxButtonsHint = _enum(Qt, 'WindowType', 'WindowMinMaxButtonsHint')

def enum_int(value):
    """Integer representation for both SIP/Qt5 enums and Python/Qt6 enums."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(value.value)

def dialog_exec(dialog):
    """Execute a modal dialog on both PyQt5 and PyQt6."""
    fn = getattr(dialog, "exec", None)
    if callable(fn):
        return fn()
    legacy_fn = getattr(dialog, "exec_", None)
    if callable(legacy_fn):
        return legacy_fn()
    raise AttributeError("Dialog object exposes neither exec() nor exec_()")

__all__ = ["QC", "QAction", "QShortcut", "dialog_exec", "enum_int"]
