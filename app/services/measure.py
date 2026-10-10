"""Measurement of a single geometry.

Flow: file CRS -> WGS84 (to find the location) -> local UTM zone -> measure in metres.
Degrees are never used for area/length.
"""
from dataclasses import dataclass, field

from pyproj import CRS
from shapely.geometry.base import BaseGeometry
import numpy as np
import shapely
from shapely.validation import explain_validity, make_valid

from .crs import WGS84, get_transformer, utm_epsg_for

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
POINTLIKE = {"Point"}


@dataclass
class Measurement:
    status: str  # OK | NOT_APPLICABLE | UNSUPPORTED | ERROR
    values: dict = field(default_factory=dict)
    measurement_crs: str | None = None
    message: str | None = None


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> Measurement:
    if geom is None or geom.is_empty:
        return Measurement("UNSUPPORTED", message="empty or missing geometry")

    gtype = geom.geom_type
    if gtype in POINTLIKE:
        return Measurement("NOT_APPLICABLE", message="points have no area or length")
    if gtype not in POLYGONAL and gtype not in LINEAR:
        return Measurement("UNSUPPORTED", message=f"measurement not supported for {gtype}")

    try:
        # 1. Locate the feature on the globe to choose a suitable UTM zone.
        to_wgs = get_transformer(src_crs, WGS84)
        centre = _reproject(geom, to_wgs).centroid
        if not (-180 <= centre.x <= 180 and -90 <= centre.y <= 90) or centre.is_empty:
            return Measurement("ERROR", message="coordinates are not valid for the declared CRS")
        utm = CRS.from_epsg(utm_epsg_for(centre.x, centre.y))

        # 2. Project the original geometry straight into that UTM zone (single hop, no double rounding).
        projected = _reproject(geom, get_transformer(src_crs, utm))

        notes = []
        if gtype in POLYGONAL and not projected.is_valid:
            notes.append(f"invalid polygon ({explain_validity(projected)}); repaired before measuring")
            projected = make_valid(projected)

        # 3. Measure in metres.
        if gtype in POLYGONAL:
            area = projected.area
            values = {
                "area_sq_m": round(area, 4),
                "area_hectares": round(area / 10_000, 6),
                "perimeter_m": round(geom_perimeter(projected), 4),
            }
        else:
            values = {"length_m": round(projected.length, 4)}
        return Measurement("OK", values, f"EPSG:{utm.to_epsg()}", "; ".join(notes) or None)
    except Exception as exc:  # never let one bad feature break the whole file
        return Measurement("ERROR", message=f"measurement failed: {exc}")


def geom_perimeter(projected: BaseGeometry) -> float:
    """Total boundary length (exterior + holes)."""
    return projected.boundary.length


def _reproject(geom: BaseGeometry, transformer) -> BaseGeometry:
    """Apply a pyproj Transformer to every vertex (Z, if any, is dropped)."""
    def fn(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])
    return shapely.transform(shapely.force_2d(geom), fn)
