from pathlib import Path
import configparser
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "QCALVIEW_ALPHA-40.20.3_QGIS_EXPERIMENTAL.zip"
EXCLUDE_DIRS = {"scripts", ".github", "tests", "__pycache__", ".git"}
EXCLUDE_FILES = {".gitignore", ".bandit", "CHANGELOG.md", "CONTRIBUTING.md", "i18n/build_catalogs.py", "i18n/QCALVIEW.pro", "i18n/README.md", "docs/DIAGNOSTIC_PROFONDEUR.md"}

def include(path):
    rel = path.relative_to(ROOT)
    return not any(part in EXCLUDE_DIRS for part in rel.parts) and rel.as_posix() not in EXCLUDE_FILES and path.suffix.lower() not in {".pyc", ".pyo", ".bak", ".tmp"}

def main():
    cfg=configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT/"metadata.txt", encoding="utf-8")
    g=cfg["general"]
    if g.get("version","").strip() != "40.20.3" or g.get("experimental","").strip().lower() != "true":
        raise SystemExit("metadata.txt must declare version=40.20.3 and experimental=True")
    with zipfile.ZipFile(OUT,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as zf:
        for p in sorted(ROOT.rglob("*")):
            if p.is_file() and include(p):
                zf.write(p,(Path("QCALVIEW")/p.relative_to(ROOT)).as_posix())
    print(OUT)

if __name__ == "__main__":
    main()
