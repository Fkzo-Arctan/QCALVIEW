# QCALVIEW — Current limitations

This document describes the main technical constraints and known limitations of the current **ALPHA-40.20.3** release.

QCALVIEW is under active development. These limitations may evolve as the rendering engine, panoramic projections, raster support and export workflows are consolidated.

## Coordinate reference systems

QCALVIEW performs distance, azimuth, elevation and projection calculations in the working/project coordinate space.

A **projected project CRS using metric units** is strongly recommended, for example EPSG:2154 — RGF93 / Lambert-93 in France.

Source viewpoint and vector layers may use another correctly declared CRS, including EPSG:4326, because QCALVIEW/QGIS transform supported data to the working project CRS. Incorrectly declared CRS definitions or a project CRS using angular units can still produce incorrect distances, offsets or placement.

## Georeferencing and source data

QCALVIEW assumes that the spatial data used for projection are correctly georeferenced.

Particular attention should be paid to:

- project CRS;
- layer CRS;
- terrain model CRS;
- elevation units;
- camera or viewpoint coordinates;
- vector geometry positions;
- raster georeferencing.

Incorrect source coordinates or inconsistent CRS definitions can directly affect the visual result.

## Images and memory usage

The compressed size of a JPEG, PNG or WebP file does not represent the amount of memory required once the image is decoded.

For example, a photograph around **12000 × 6000 pixels** can require several hundred megabytes of memory during processing.

QCALVIEW may use lower-resolution proxies for interactive display, but large source images can still increase memory consumption during loading, rendering and export.

For interactive work, avoid unnecessarily oversized images when a lower working resolution is sufficient.

## Terrain models

Performance depends strongly on the size and resolution of the terrain dataset.

Very large or very fine DEM/DTM/DSM datasets can increase:

- loading time;
- horizon calculations;
- visibility calculations;
- z-buffer generation;
- memory usage;
- export time.

Use a terrain resolution appropriate to the required output scale whenever possible.

## Dense vector datasets

Large vector layers or geometrically complex features can significantly increase rendering time.

This is especially relevant for:

- large polygon datasets;
- dense vegetation representations;
- large numbers of point symbols;
- complex SVG motifs;
- extruded geometries;
- features requiring visibility or occlusion calculations.

Filtering the working extent and limiting unnecessary features can improve performance.

## Projection modes

### Pinhole / rectilinear

The pinhole / rectilinear rendering path is currently the most mature projection mode in QCALVIEW.

It should be preferred when maximum rendering stability is required.

### Cylindrical and equirectangular projections

Panoramic rendering is supported but remains under active development.

Known areas still being consolidated include:

- depth handling;
- z-buffer consistency;
- object positioning across very wide fields of view;
- behaviour near the ±180° seam;
- object size and placement near panorama edges;
- consistency between interactive rendering and exported output;
- performance on large panoramic scenes.

Panoramic results should therefore be visually checked before delivery or publication.

## SVG and schematic patterns

QCALVIEW can use SVG artwork for schematic vegetation, infrastructure and other repeated motifs.

Depending on the rendering path, some SVG elements may undergo rasterisation or resampling.

Possible consequences include:

- reduced sharpness at small display sizes;
- softness compared with the original SVG;
- increased memory consumption for dense patterns;
- increased rendering time for large quantities of repeated motifs.

SVG handling and hybrid high-quality rendering remain active development areas.

## Occlusion and visibility

Visibility and occlusion calculations depend on terrain resolution, geometry complexity, rendering mode and z-buffer precision.

Results should not be treated as a substitute for dedicated regulatory visibility analysis without appropriate verification.

For critical applications, compare the result with source GIS data and independent visibility calculations where relevant.

## Interactive viewer

Interactive display is optimised for responsive work rather than always reproducing final export quality at full source resolution.

Depending on the dataset and current development state:

- some updates may require an explicit refresh;
- very dense scenes may not update instantly;
- interactive proxies can differ in sharpness from final exports.

## Exports

High-resolution exports can require substantially more memory and processing time than the interactive viewer.

Before producing very large final images, test the scene at an intermediate output resolution.

In the current alpha series, exported output should also be compared with the interactive view, particularly when using:

- panoramic projections;
- vertical offsets;
- complex schematic patterns;
- occlusion;
- large terrain datasets.

## Experimental tools

The public experimental build intentionally exposes only a limited subset of development tools:

- Projection grid
- Azimuth ruler
- Interactive monoplotting

Other internal tools remain hidden or disabled until they are sufficiently stable for wider testing.

## Performance

QCALVIEW is currently implemented as a QGIS/Python plugin and is intended as a functional visual simulation and geomatics tool rather than a GPU-native real-time rendering engine.

Performance varies depending on:

- image resolution;
- output resolution;
- terrain size and resolution;
- vector feature count;
- geometry complexity;
- SVG pattern density;
- visibility and occlusion calculations;
- projection mode.

Large scenes may therefore require simplified working data or reduced preview quality.

## Production use

QCALVIEW ALPHA releases should not be considered validated measurement software.

The operator remains responsible for checking:

- camera calibration;
- viewpoint position;
- camera orientation;
- terrain consistency;
- projected feature placement;
- output scale;
- visual coherence;
- final exported documents.

Visual simulations should be reviewed before being used in professional, contractual, regulatory or public-facing material.

## Reporting issues

When reporting a rendering or projection issue, provide whenever possible:

- QCALVIEW version;
- QGIS version;
- operating system;
- projection mode;
- project CRS;
- image dimensions;
- terrain type and resolution;
- affected layer type;
- relevant screenshots;
- QGIS log or Python traceback if available.

This information greatly improves reproducibility and debugging.
