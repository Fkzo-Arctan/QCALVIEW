"""Vertical datum checks and camera-height interpretation (QCALVIEW).

No geoid correction, pressure correction, or vertical transformation is applied.
Unknown raster height datums must NOT be inferred from their horizontal CRS.
"""
from __future__ import annotations
import math

VERTICAL_MODE_KEY = 'vertical_reference_mode'
VERTICAL_CRS_KEY = 'vertical_reference_authid'


def camera_z(terrain_z, entered_z, absolute=False):
    """Return camera Z with the historical DEM+AGL convention as default."""
    z = float(entered_z)
    if not math.isfinite(z):
        raise ValueError('Camera altitude is not finite')
    if absolute:
        return z
    g = float(terrain_z)
    if not math.isfinite(g):
        raise ValueError('Ground elevation is not finite')
    return g + z


def _vertical_component(crs):
    """Extract a vertical CRS, never 'guess' one from EPSG:2154 etc."""
    if crs is None or not crs.isValid():
        return None
    # A vertical-only CRS is itself the datum. Compound CRS can expose it.
    try:
        from qgis.core import Qgis
        if crs.type() == Qgis.CrsType.Vertical:
            return crs
    except (AttributeError, TypeError):
        pass
    try:
        component = crs.verticalCrs()
        if component is not None and component.isValid():
            return component
    except (AttributeError, TypeError):
        pass
    return None


def configured_vertical_crs(project):
    """Read project-scoped selection. Return (vertical_crs_or_None, source)."""
    from qgis.core import QgsCoordinateReferenceSystem
    mode = str(project.readEntry('QCALVIEW', VERTICAL_MODE_KEY, 'project')[0] or 'project')
    if mode == 'custom':
        ident = str(project.readEntry('QCALVIEW', VERTICAL_CRS_KEY, '')[0] or '').strip()
        return _vertical_component(QgsCoordinateReferenceSystem(ident)), 'custom'
    try:
        return _vertical_component(project.verticalCrs()), 'project'
    except AttributeError:
        return None, 'project'


def raster_vertical_crs(layer):
    """Return DEM vertical CRS only if explicitly declared in the layer CRS."""
    if layer is None or not layer.isValid():
        return None
    try:
        explicit = layer.verticalCrs()
        component = _vertical_component(explicit)
        if component is not None:
            return component
    except AttributeError:
        pass
    try:
        return _vertical_component(layer.crs())
    except (AttributeError, TypeError):
        return None


def vertical_comparison(camera_vertical_crs, dem_vertical_crs):
    """'compatible', 'unknown', or 'mismatch'. Never convert heights implicitly."""
    if camera_vertical_crs is None or dem_vertical_crs is None:
        return 'unknown'
    try:
        if camera_vertical_crs == dem_vertical_crs:
            return 'compatible'
        ca = camera_vertical_crs.authid()
        da = dem_vertical_crs.authid()
        if ca and da and str(ca).upper() == str(da).upper():
            return 'compatible'
        return 'mismatch'
    except (AttributeError, TypeError):
        return 'unknown'
