# QCALVIEW — Public changelog

## ALPHA-40.20.5
- Fixes Interactive Monoplotting clicks in the full viewer, including proxy-image coordinate mapping and point-picking interaction.
- Keeps preview and full-viewer image picking consistent for monoplotting and image-to-map tools.
- Generalises the curvature radius control to a configurable body radius while retaining 6370.0 km as the default and preserving existing project/settings compatibility.
- Completes English-default localisation coverage across the public interface, messages, logs and experimental tools.
- Includes repository-oriented code and packaging cleanup.

## ALPHA-40.20.4
- Add message to force selection of dtm
- Experimental release for QGIS 3.44/Qt5 and QGIS 4.x/Qt6.
- Adds automatic PDV colors and an editable manual style mode stored per layer/project.
- Supports partial EQUIRECT fields of view while retaining explicit 360x180 mode; CYLINDRICAL 360° remains independent from 360x180.
- Improves CRS-consistent PDV FOV geometry and the exact 180° sector case.
- Improves PANORAMA depth handling, terrain masking, horizon reuse and memory behavior.
- QCALVIEW temporarily disables QGIS canvas preview jobs while its dock is open, restoring the previous setting on close, to avoid a renderer-cloning crash observed in QGIS 3.44.
- Many, many, many bugs and access violations....

This release remains experimental and should be validated on representative projects before production use.
