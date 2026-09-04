# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ..core._i18n import tr
from ..core._compat import QC, dialog_exec
# ui/widgets.py — QGIS 4 / Qt6 compatible (uses QGIS PyQt shim)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QWidget, QToolButton, QSizePolicy, QVBoxLayout

class CollapsibleBox(QWidget):
    # API miroir QGroupBox utilisée par le dock QCALVIEW.
    toggled = pyqtSignal(bool)

    def __init__(self, title: str = "", *, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checkable = True
        self.toggle_button = QToolButton(text=title, checkable=True, checked=checked)
        self.toggle_button.setToolButtonStyle(QC.Qt_ToolButtonStyle_ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QC.Qt_ArrowType_DownArrow)
        self.toggle_button.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Fixed)
        self.content_area = QWidget()
        self.content_area.setSizePolicy(QC.QSizePolicy_Policy_Expanding, QC.QSizePolicy_Policy_Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.toggle_button)
        lay.addWidget(self.content_area)
        self.toggle_button.toggled.connect(self._on_toggled)
        self.toggle_button.toggled.connect(self.toggled)
        self._on_toggled(checked)

    def setContentLayout(self, layout):
        self.content_area.setLayout(layout)

    def setTitle(self, title: str):
        self.toggle_button.setText(tr(title))

    def title(self) -> str:
        return self.toggle_button.text()

    def setChecked(self, checked: bool):
        if not self._checkable:
            checked = True
        self.toggle_button.setChecked(checked)

    def isChecked(self) -> bool:
        return True if not self._checkable else self.toggle_button.isChecked()

    def setCheckable(self, checkable: bool):
        self._checkable = bool(checkable)
        self.toggle_button.setCheckable(self._checkable)
        if self._checkable:
            self.toggle_button.setArrowType(
                QC.Qt_ArrowType_DownArrow if self.toggle_button.isChecked() else QC.Qt_ArrowType_RightArrow
            )
            self.content_area.setVisible(self.toggle_button.isChecked())
            self.content_area.setEnabled(self.toggle_button.isChecked())
        else:
            self.toggle_button.setArrowType(QC.Qt_ArrowType_NoArrow)
            self.content_area.setVisible(True)
            self.content_area.setEnabled(True)

    def isCheckable(self) -> bool:
        return self._checkable

    def _on_toggled(self, checked: bool):
        self.toggle_button.setArrowType(
            QC.Qt_ArrowType_DownArrow if checked else QC.Qt_ArrowType_RightArrow
        )
        self.content_area.setVisible(checked)
