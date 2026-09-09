




from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "i18n"


def find_lrelease():
    candidates = ["lrelease", "lrelease6", "lrelease-qt6", "lrelease-qt5", "lrelease.exe"]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def main():
    exe = find_lrelease()
    if not exe:
        print("Qt Linguist lrelease was not found in PATH.", file=sys.stderr)
        print("Run this script from the QGIS/OSGeo4W shell or a Qt development environment.", file=sys.stderr)
        return 2
    catalogs = sorted(I18N.glob("QCALVIEW_*.ts"))
    if not catalogs:
        print("No QCALVIEW .ts catalogs found.", file=sys.stderr)
        return 1
    for ts in catalogs:
        qm = ts.with_suffix(".qm")
        subprocess.run([exe, str(ts), "-qm", str(qm)], check=True)
        print(f"Compiled {ts.name} -> {qm.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
