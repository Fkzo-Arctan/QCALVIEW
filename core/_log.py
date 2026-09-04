# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""Centralised QCALVIEW logging to the QGIS message log."""
from __future__ import annotations
from ._i18n import tr


def qcv_log(message, section="CORE", level="INFO"):
    text = f"[QCALVIEW][{str(section).upper()}] {tr(message)}"
    try:
        from qgis.core import QgsMessageLog, Qgis
        levels = {
            "INFO": getattr(Qgis, "Info", 0),
            "WARNING": getattr(Qgis, "Warning", 1),
            "CRITICAL": getattr(Qgis, "Critical", 2),
            "SUCCESS": getattr(Qgis, "Success", getattr(Qgis, "Info", 0)),
        }
        QgsMessageLog.logMessage(text, "QCALVIEW", levels.get(str(level).upper(), levels["INFO"]))
    except Exception:
        # Logging must never affect rendering/plugin startup.
        pass
