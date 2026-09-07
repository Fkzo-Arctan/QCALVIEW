<p align="center">
  <img src="docs/images/qcalview-banner.webp"
       alt="QCALVIEW - Overlay photos and vector data"
       width="100%">
</p>

# QCALVIEW — ALPHA-40.20

**Visual Simulation & Geomatics for QGIS**

QCALVIEW is an open-source QGIS plugin developed by **Fabrice Kerzerho — ArcTan°** for photographic viewpoint calibration, visual simulation, projected GIS overlays and rapid landscape representations directly inside QGIS.

- Project: https://qcalview.com
- ArcTan°: https://arctan.fr
- Contact: contact@arctan.fr

## Compatibility

- QGIS 3.44.x / Qt5
- QGIS 4.x / Qt6

## Requirements and current limitations

QCALVIEW is currently an experimental plugin. For reliable results:

- Use a **projected CRS with metric units** (for example Lambert-93 / EPSG:2154 in France). Geographic coordinate systems such as EPSG:4326 are not recommended for QCALVIEW calculations and visual projection.
- Use correctly georeferenced terrain, raster and vector datasets.
- Very large source photographs can require significant memory once decoded. Reasonably sized images are recommended for interactive work.
- High-resolution exports, complex SVG patterns, dense vector layers and large terrain datasets can significantly increase rendering time and memory usage.
- **Pinhole / rectilinear rendering is currently the most mature projection mode.**
- Cylindrical and equirectangular panoramic projections are still under active development and may present rendering, depth, edge or positioning differences in some configurations.
- Some experimental tools are intentionally hidden from the public experimental release.
- Visual simulations and exported documents should be checked by the operator before production use.

For more detail, see [Current limitations](docs/limitations.md).

## Experimental status

The current **ALPHA-40.20** branch is prepared for publication as an **experimental QGIS plugin**. It remains under active development; interfaces and experimental behaviours may change before the first consolidated release. It should be tested on representative projects before production use.

For public testing, the **Experimental Tools** tab exposes only:

- Projection grid
- Azimuth ruler
- Interactive monoplotting

Other internal experimental modules remain development-only until they are ready for wider testing.

## Open-source development

QCALVIEW is made available as open-source software. Its development, maintenance, QGIS compatibility work, bug fixing and new capabilities are sponsored primarily by ArcTan° and may also be supported by users and partners.

Development of QCALVIEW can be supported through GitHub Sponsors when the sponsorship profile becomes available.

## Documentation

See `docs/` for the user manual, current limitations and licensing notes.

## Licence

QCALVIEW is licensed under the **GNU GPL v3.0 or later**. Copyright © 2026 Fabrice Kerzerho — ArcTan°.

Redistributions should retain the applicable attribution described in `NOTICE`. QCALVIEW, ArcTan° and their associated logos/visual identity are not licensed for unrestricted branding use under the GPL. See `TRADEMARKS.md`.
