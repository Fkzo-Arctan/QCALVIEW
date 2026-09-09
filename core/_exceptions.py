from __future__ import annotations

import os

_DEBUG_EXCEPTIONS = os.environ.get("QCALVIEW_DEBUG_EXCEPTIONS", "").strip().lower() in {"1", "true", "yes", "on"}


def qcv_suppress_exception(exc, context=""):
    if not _DEBUG_EXCEPTIONS:
        return None
    try:
        from qgis.core import QgsMessageLog, Qgis
        level = getattr(Qgis, "Info", 0)
        QgsMessageLog.logMessage(
            f"[QCALVIEW][SUPPRESSED] {context}: {type(exc).__name__}: {exc}",
            "QCALVIEW",
            level,
        )
    except Exception:
        return None
    return None
