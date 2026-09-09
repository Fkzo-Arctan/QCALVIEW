


from __future__ import annotations
from ._compat import QC, dialog_exec

from dataclasses import dataclass
from qgis.core import QgsWkbTypes


@dataclass
class BudgetProfile:
    name: str
    target_score: int
    hard_score: int
    hard_features: int
    hard_vertices: int
    default_search_ratios: tuple[float, ...]


@dataclass
class LayerEstimate:
    layer_id: str
    feature_count_total: int
    candidate_count: int
    estimated_vertices: int
    geom_factor: float
    style_factor: float
    labels_factor: float
    extrusion_factor: float
    score: float
    needs_simplification: bool = False


@dataclass
class RenderDecision:
    accepted: bool
    effective_maxdist: float
    degrade_labels: bool
    degrade_style: bool
    simplify_geometry: bool
    reason: str
    score: float
    candidate_count: int
    estimated_vertices: int


BUDGET_SAFE = BudgetProfile(
    name="safe",
    target_score=2500,
    hard_score=7000,
    hard_features=700,
    hard_vertices=90000,
    default_search_ratios=(1.0, 0.5, 0.25, 0.125),
)

BUDGET_BALANCED = BudgetProfile(
    name="balanced",
    target_score=5000,
    hard_score=12000,
    hard_features=1400,
    hard_vertices=160000,
    default_search_ratios=(1.0, 0.6, 0.35, 0.2),
)

BUDGET_DETAIL = BudgetProfile(
    name="detail",
    target_score=9000,
    hard_score=18000,
    hard_features=2200,
    hard_vertices=260000,
    default_search_ratios=(1.0, 0.7, 0.5, 0.3),
)


def get_budget_profile(value) -> BudgetProfile:
    txt = str(value or "balanced").strip().lower()
    if txt.startswith("s"):
        return BUDGET_SAFE
    if "detail" in txt or txt.startswith("m"):
        return BUDGET_DETAIL
    return BUDGET_BALANCED


def geom_factor_from_gtype(gtype: int, is_multipart: bool = False) -> float:
    if gtype == QC.QgsWkbTypes_GeometryType_PointGeometry:
        base = 1.0
    elif gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
        base = 1.3
    else:
        base = 2.0
    if is_multipart:
        base *= 1.25
    return float(base)


def style_factor_from_style(sty, degrade_style: bool = False) -> float:
    if degrade_style:
        return 1.0
    spec = None
    if bool(getattr(sty, 'use_qgis_style', True)):
        spec = getattr(sty, 'qgis_fill_style', None)
    kind = str((spec or {}).get('kind', 'simple') or 'simple').lower() if spec else 'simple'
    if kind == 'line_pattern':
        return 1.8
    if kind == 'point_pattern':
        return 2.1
    if kind == 'gradient':
        return 1.7
    if kind not in ('simple', 'solid'):
        return 2.4
    return 1.1 if getattr(sty, 'fill_polygons', True) else 1.0


def extrusion_factor_for_style(sty, gtype: int, draw_25d: bool = False) -> float:
    if not draw_25d or (not bool(getattr(sty, 'enable_25d', True))):
        return 1.0
    if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
        return 1.8
    if gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
        return 1.5
    return 1.15


def default_interactive_distance(gtype: int, draw_25d: bool = False) -> float:
    if gtype == QC.QgsWkbTypes_GeometryType_PolygonGeometry:
        return 1200.0 if draw_25d else 5000.0
    if gtype == QC.QgsWkbTypes_GeometryType_LineGeometry:
        return 1500.0 if draw_25d else 3000.0
    return 3000.0


def estimate_score(candidate_count: int,
                   estimated_vertices: int,
                   geom_factor: float,
                   style_factor: float,
                   labels_factor: float,
                   extrusion_factor: float,
                   transparency_factor: float = 1.0,
                   simplify_geometry: bool = False) -> float:
    base = float(candidate_count) * float(geom_factor) * float(style_factor) * float(labels_factor) * float(extrusion_factor) * float(transparency_factor)
    vertex_factor = 0.018 if not simplify_geometry else 0.010
    vertex_component = float(estimated_vertices) * vertex_factor * max(1.0, float(style_factor) * 0.85) * max(1.0, float(extrusion_factor) * 0.75)
    return float(base + vertex_component)
