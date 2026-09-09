



import os, sys, math, json, re, pathlib, functools, itertools, typing
from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.core import *
from qgis.gui import *


try:
    from qgis.PyQt.QtGui import QImage, QPainter, QPen, QColor, QFont, QPixmap, QTransform
except Exception:
    QImage = QtGui.QImage; QPainter = QtGui.QPainter; QPen = QtGui.QPen
    QColor = QtGui.QColor; QFont = QtGui.QFont; QPixmap = QtGui.QPixmap
    QTransform = getattr(QtGui, 'QTransform', None)
try:
    from qgis.PyQt.QtCore import Qt, QSize, QRect, QPointF, QPoint
except Exception:
    Qt = getattr(QtCore, 'Qt', None); QSize = QtCore.QSize; QRect = QtCore.QRect
    QPointF = QtCore.QPointF; QPoint = QtCore.QPoint
try:
    from qgis.PyQt.QtWidgets import QWidget, QLabel
except Exception:
    QWidget = QtWidgets.QWidget; QLabel = QtWidgets.QLabel

try:
    from ._utils_ops import hfov_from_focal_sensor, vfov_from_hfov_ratio
except Exception:
    
    def hfov_from_focal_sensor(f, sw):
        try: return self.hfov_from_focal_sensor(f, sw)  
        except Exception: return 0.0
    def vfov_from_hfov_ratio(h, w, ht):
        try: return self.vfov_from_hfov_ratio(h, w, ht)  
        except Exception: return 0.0


def _toggle_hfov_enable(self, checked):
    self.d_hfov.setEnabled(not checked)
    if checked and self.image is not None:
        HFOV = hfov_from_focal_sensor(self.d_focal.value(), self.d_sensorw.value())
        self.d_hfov.blockSignals(True); self.d_hfov.setValue(HFOV); self.d_hfov.blockSignals(False)
        try:
            proj_txt = str(getattr(self, 'cmb_proj', None).currentText()).strip().upper() if getattr(self, 'cmb_proj', None) else ''
        except Exception:
            proj_txt = ''
        vfov = 40.0 if proj_txt == 'CYLINDRICAL' else vfov_from_hfov_ratio(HFOV, self.spin_w.value(), self.spin_h.value())
        self.d_vfov.blockSignals(True); self.d_vfov.setValue(vfov); self.d_vfov.blockSignals(False)
        self.render_preview()

def _get_projection_mode(self) -> str:
    
    cmb = getattr(self, "cmb_projection", None)
    if cmb is not None:
        try:
            txt = cmb.currentText().strip().upper()
            if txt in ("PINHOLE", "EQUIRECT", "CYLINDRICAL"):
                return txt
        except Exception:
            pass
    return getattr(self, "_projection_mode", "PINHOLE")
