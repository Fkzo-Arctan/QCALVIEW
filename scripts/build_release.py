#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a clean QGIS plugin ZIP from the development tree."""
from __future__ import annotations

import argparse
import configparser
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "QCALVIEW"
EXCLUDED_DIRS = {"dev_docs", "scripts", ".github", "__pycache__", ".git", ".idea", ".vscode"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".bak", ".tmp"}
EXCLUDED_FILES = {".gitignore", "CHANGELOG.md", "CONTRIBUTING.md", "i18n/build_catalogs.py", "i18n/QCALVIEW.pro", "i18n/README.md"}


def metadata_errors(allow_incomplete=False):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT / "metadata.txt", encoding="utf-8")
    g = cfg["general"]
    errors = []
    if not (ROOT / "LICENSE").is_file():
        errors.append("LICENSE is missing")
    if g.get("experimental", "").strip().lower() != "true":
        errors.append("experimental=True is required for this candidate")
    for key in ("repository", "tracker"):
        if not g.get(key, "").strip() and not allow_incomplete:
            errors.append(f"metadata.txt: {key}= is empty")
    icon = g.get("icon", "").strip()
    if not icon or not (ROOT / icon).is_file():
        errors.append("metadata icon is missing")
    return errors


def include_file(path: Path):
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if rel.as_posix() in EXCLUDED_FILES:
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "QCALVIEW_ALPHA-40.20_QGIS_EXPERIMENTAL.zip")
    parser.add_argument("--allow-incomplete-metadata", action="store_true",
                        help="Build an inspection ZIP even if repository/tracker are still blank.")
    args = parser.parse_args()
    errors = metadata_errors(args.allow_incomplete_metadata)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(ROOT.rglob("*")):
            if path.is_file() and include_file(path):
                rel = path.relative_to(ROOT)
                arcname = Path(PLUGIN_NAME) / rel
                if rel.as_posix() == "core/_release.py":
                    text = path.read_text(encoding="utf-8")
                    text = text.replace("PUBLIC_EXPERIMENTAL_LIMITED = False", "PUBLIC_EXPERIMENTAL_LIMITED = True")
                    zf.writestr(arcname.as_posix(), text.encode("utf-8"))
                else:
                    zf.write(path, arcname.as_posix())
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
