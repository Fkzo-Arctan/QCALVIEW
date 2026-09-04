# QCALVIEW schematic library — AVR 0/1

JSON files describe parametric 2.5D schematic generators. SVG files are vector
resources for point objects (billboards); they are not a 3D scene. Dimensions
are expressed in real-world metres and then projected by the QCALVIEW camera.

## Billboard dimensions

- `height_m`: real symbol height.
  - By default, it may follow the layer's 2.5D height field/parameter.
  - In **Style… > Schematic representation > Settings…**, disabling **Layer height**
    allows a symbol-specific height to be set.
- `width_m`: real symbol width. An optional `qcv_width` field may control it per
  feature; otherwise the library value or layer override is used.

## Included symbols

### Vegetation
- deciduous tree;
- spruce/fir conifer;
- pine;
- shrub / low thicket;
- generic parametric hedge (line geometry).

### Agriculture and animals
- tractor;
- combine harvester;
- cattle;
- sheep;
- horse;
- poultry.

### Reference objects
- person;
- car.

### Renewable energy and structures
- parametric photovoltaic table;
- parametric wind turbine;
- extruded volume.

## Extension

To add a new point object, copy a `billboard_svg` JSON file, change `id`, `name`,
default dimensions and the path to an SVG stored in `assets/`. SVG files must use
a transparent background and place their ground contact point at the bottom of the
`viewBox`.
