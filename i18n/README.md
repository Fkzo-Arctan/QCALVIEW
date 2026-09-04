# QCALVIEW translations

QCALVIEW uses the standard Qt/QGIS translation mechanism (`QTranslator`, `.ts`, `.qm`).

- Historical source strings are French.
- A French QGIS/system locale uses the source strings directly.
- Any other locale falls back to English.
- `QCALVIEW_en.ts` contains the English catalog.
- `QCALVIEW_fr.ts` is an identity French catalog kept for maintenance and Qt Linguist workflows.

## Update catalogs

Run:

```bash
python i18n/build_catalogs.py
```

The builder includes a bilingual audit and must report `review hints remaining: 0` before release.

## Compile `.qm`

From a QGIS/OSGeo4W shell or Qt development environment:

```bash
python scripts/compile_translations.py
```

If `lrelease` is not available, QCALVIEW can still use the bundled English `.ts` catalog through its runtime fallback. Official `.qm` catalogs should nevertheless be generated before repository publication when possible.
