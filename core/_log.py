



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
        
        pass
