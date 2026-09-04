# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ._i18n import tr
from ._compat import QC, dialog_exec
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QColorDialog, QGroupBox, QFormLayout, QLineEdit, QScrollArea
)

# Dialogues simples
class QInputDialogWithDefault:
    @staticmethod
    def getDouble(parent, title, label, value, minv, maxv, decimals):
        from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QDoubleSpinBox, QLabel
        dlg = QDialog(parent); dlg.setWindowTitle(tr(title))
        v = QVBoxLayout(dlg); v.addWidget(QLabel(tr(label)))
        sp = QDoubleSpinBox(); sp.setRange(minv, maxv); sp.setDecimals(decimals); sp.setValue(value); v.addWidget(sp)
        bb = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel); v.addWidget(bb)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        if dialog_exec(dlg) == QC.QDialog_DialogCode_Accepted: return sp.value(), True
        return value, False

    @staticmethod
    def getText(parent, title, label, text):
        from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QLineEdit, QLabel
        dlg = QDialog(parent); dlg.setWindowTitle(tr(title))
        v = QVBoxLayout(dlg); v.addWidget(QLabel(tr(label)))
        le = QLineEdit(text); v.addWidget(le)
        bb = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel); v.addWidget(bb)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        if dialog_exec(dlg) == QC.QDialog_DialogCode_Accepted: return le.text(), True
        return text, False

    @staticmethod
    def getInt(parent, title, label, value, minv, maxv, step):
        from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QSpinBox, QLabel
        dlg = QDialog(parent); dlg.setWindowTitle(tr(title))
        v = QVBoxLayout(dlg); v.addWidget(QLabel(tr(label)))
        sp = QSpinBox(); sp.setRange(minv, maxv); sp.setSingleStep(step); sp.setValue(value); v.addWidget(sp)
        bb = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel); v.addWidget(bb)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        if dialog_exec(dlg) == QC.QDialog_DialogCode_Accepted: return sp.value(), True
        return value, False

    @staticmethod
    def getChoice(parent, title, label, options, current_index=0):
        from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QLabel, QComboBox
        dlg = QDialog(parent); dlg.setWindowTitle(tr(title))
        v = QVBoxLayout(dlg); v.addWidget(QLabel(tr(label)))
        cb = QComboBox(); cb.addItems(tr(options))
        if 0 <= current_index < len(options): cb.setCurrentIndex(current_index)
        v.addWidget(cb)
        bb = QDialogButtonBox(QC.QDialogButtonBox_StandardButton_Ok | QC.QDialogButtonBox_StandardButton_Cancel); v.addWidget(bb)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        if dialog_exec(dlg) == QC.QDialog_DialogCode_Accepted: return cb.currentText(), True
        return options[current_index], False
