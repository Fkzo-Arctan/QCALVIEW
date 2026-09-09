



from __future__ import annotations
from ._i18n import tr
from ._compat import QC, dialog_exec

import functools
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, List

try:
    from qgis.core import QgsMessageLog, Qgis
except Exception:  
    QgsMessageLog = None
    Qgis = None


class LightProfiler:
    def __init__(self):
        self.enabled = False
        self._stats = defaultdict(list)

    @contextmanager
    def section(self, name: str):
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._stats[name].append(elapsed_ms)

    def profile(self, name: str):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with self.section(name):
                    return func(*args, **kwargs)
            return wrapper
        return decorator

    def reset(self):
        self._stats.clear()

    def get_report(self) -> Dict[str, Dict[str, float]]:
        report: Dict[str, Dict[str, float]] = {}
        for name, values in self._stats.items():
            if not values:
                continue
            total = float(sum(values))
            count = len(values)
            report[name] = {
                'count': count,
                'total_ms': total,
                'avg_ms': total / count,
                'min_ms': min(values),
                'max_ms': max(values),
            }
        return dict(sorted(report.items(), key=lambda kv: kv[1]['total_ms'], reverse=True))

    def report_text(self) -> str:
        report = self.get_report()
        if not report:
            return "Aucune donnée de profiling enregistrée."
        lines: List[str] = ["Rapport de performance", ""]
        for name, stats in report.items():
            lines.append(
                f"• {name} — appels: {int(stats['count'])}, "
                f"moy: {stats['avg_ms']:.1f} ms, "
                f"min: {stats['min_ms']:.1f} ms, "
                f"max: {stats['max_ms']:.1f} ms, "
                f"total: {stats['total_ms']:.1f} ms"
            )
        return "\n".join(lines)

    def log_report(self, tag: str = 'FMV_Photo'):
        text = self.report_text()
        if QgsMessageLog is not None and Qgis is not None:
            QgsMessageLog.logMessage(tr(text), tag, QC.Qgis_MessageLevel_Info)
        else:
            print(text)


_PROFILER = LightProfiler()


def get_profiler() -> LightProfiler:
    return _PROFILER
