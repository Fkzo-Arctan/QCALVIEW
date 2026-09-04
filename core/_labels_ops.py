# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
from ._compat import QC, dialog_exec
"""SPLIT-ONLY extracted implementations from qcalview_window.QCalViewDock.
Attached to the class via setattr after class definition.
"""
import os, sys, math, json, re, pathlib, functools, itertools, typing
from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.core import *
from qgis.gui import *
from ..projector import project_point

# --- Explicit Qt imports ---
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


def _label_offset_from_pos(self, pos, base_px=8):
    pos = (pos or "NE").upper()
    dx = dy = 0
    if pos == "N":  dy = -base_px
    if pos == "S":  dy =  base_px
    if pos == "E":  dx =  base_px
    if pos == "W":  dx = -base_px
    if pos == "NE": dx, dy = base_px, -base_px
    if pos == "NW": dx, dy = -base_px, -base_px
    if pos == "SE": dx, dy = base_px, base_px
    if pos == "SW": dx, dy = -base_px, base_px
    return dx, dy



def _resolve_label_text(self, sty, feat):
    try:
        if bool(getattr(sty, 'use_qgis_labels', False)):
            expr_txt = str(getattr(sty, 'qgis_label_expr', '') or '')
            if expr_txt:
                if bool(getattr(sty, 'qgis_label_is_expression', False)):
                    try:
                        ctx = QgsExpressionContext()
                        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(getattr(sty, 'layer', None)))
                        ctx.setFeature(feat)
                        val = QgsExpression(expr_txt).evaluate(ctx)
                        if val not in (None, ''):
                            return str(val)
                    except Exception:
                        return expr_txt
                else:
                    fld = getattr(sty, 'label_field', None) or expr_txt
                    if fld and (fld in feat.fields().names()):
                        return str(feat[fld])
            # si la couche QGIS n'a pas d'expression explicite, on retombe sur les champs manuels éventuels
        text = sty.label_text.strip() if getattr(sty, 'label_text', '') else None
        if not text and getattr(sty, 'label_field', None) and (sty.label_field in feat.fields().names()):
            text = str(feat[sty.label_field])
        return text
    except Exception:
        try:
            text = sty.label_text.strip() if getattr(sty, 'label_text', '') else None
            if not text and getattr(sty, 'label_field', None) and (sty.label_field in feat.fields().names()):
                text = str(feat[sty.label_field])
            return text
        except Exception:
            return None

def _label_anchor_uv(self, sty, feat, geom, proj, width, height, cam_pt, cam_z,
                     tr, yaw, pitch, roll, HFOV, VFOV, is360, maxdist, z_sampler, h_for_feat):
    text = _resolve_label_text(self, sty, feat)
    if not text:
        return None, None

    mode = (getattr(sty, 'label_anchor_mode', 'AUTO') or 'AUTO').upper()

    def _project_anchor_point(c):
        if c is None:
            return None
        try:
            pt_cam = tr.transform(c) if tr else c
        except Exception:
            pt_cam = c
        try:
            ground_z = z_sampler(pt_cam) if z_sampler else 0.0
        except Exception:
            ground_z = 0.0
        try:
            h = float(h_for_feat) if (h_for_feat not in (None, '')) else 0.0
        except Exception:
            h = 0.0
        zt = float(ground_z) + max(0.0, h)
        try:
            uv = self._finite_uv(project_point(cam_pt, cam_z, pt_cam, None, proj, width, height, yaw, pitch, roll,
                                               HFOV, VFOV, is360, dist_max=maxdist, z_tgt=zt, z_sampler=None))
        except Exception:
            uv = None
        return uv

    if mode == "IMAGE_CENTER":
        return (width/2.0, height/2.0), text

    c = None
    try:
        gtype = QgsWkbTypes.geometryType(geom.wkbType())
    except Exception:
        gtype = None

    if mode in ("AUTO", "CENTROID"):
        try:
            if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
                ps = geom.pointOnSurface() if hasattr(geom, 'pointOnSurface') else None
                if ps is not None and not ps.isEmpty():
                    c = ps.asPoint()
                if c is None:
                    cen = geom.centroid()
                    if cen is not None and not cen.isEmpty():
                        c = cen.asPoint()
            elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
                cen = geom.centroid()
                if cen is not None and not cen.isEmpty():
                    c = cen.asPoint()
                if c is None:
                    if geom.isMultipart():
                        lines = geom.asMultiPolyline()
                        if lines and lines[0]:
                            c = lines[0][len(lines[0]) // 2]
                    else:
                        line = geom.asPolyline()
                        if line:
                            c = line[len(line) // 2]
            elif gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
                if geom.isMultipart():
                    pts = geom.asMultiPoint()
                    c = pts[0] if pts else None
                else:
                    c = geom.asPoint()
        except Exception:
            c = None

    if c is None:
        return None, text
    return _project_anchor_point(c), text
