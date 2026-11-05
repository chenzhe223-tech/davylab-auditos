"""Deterministic, evidence-linked DXF geometry measurements.

This module measures model-space entities only. It deliberately does not infer
cable routes, vertical risers, installation allowances, or billable quantities.
"""
from __future__ import annotations

import fnmatch
import hashlib
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.lldxf.const import DXFStructureError

SUPPORTED_UNITS: dict[int, tuple[str, Decimal]] = {
    1: ("in", Decimal("0.0254")),
    2: ("ft", Decimal("0.3048")),
    3: ("mi", Decimal("1609.344")),
    4: ("mm", Decimal("0.001")),
    5: ("cm", Decimal("0.01")),
    6: ("m", Decimal(1)),
    7: ("km", Decimal(1000)),
    9: ("mil", Decimal("0.0000254")),
    10: ("yd", Decimal("0.9144")),
    21: ("us_survey_ft", Decimal(1200) / Decimal(3937)),
}
UNIT_ALIASES = {
    "millimeter": "mm", "millimeters": "mm", "毫米": "mm",
    "centimeter": "cm", "centimeters": "cm", "厘米": "cm",
    "meter": "m", "meters": "m", "metre": "m", "米": "m",
    "inch": "in", "inches": "in", "英寸": "in",
    "foot": "ft", "feet": "ft", "英尺": "ft",
}
UNIT_FACTORS = {name: factor for name, factor in SUPPORTED_UNITS.values()}
UNIT_FACTORS.update({"mm": Decimal("0.001"), "cm": Decimal("0.01"), "m": Decimal(1),
                     "in": Decimal("0.0254"), "ft": Decimal("0.3048")})


class DrawingGeometryError(ValueError):
    """Raised when a DXF cannot be measured safely."""


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 12)))


def _unit_info(doc: Any, override: str | None) -> tuple[str | None, Decimal | None]:
    if override:
        unit = UNIT_ALIASES.get(override.strip().lower(), override.strip().lower())
        factor = UNIT_FACTORS.get(unit)
        if factor is None:
            raise DrawingGeometryError(f"Unsupported source unit override: {override}")
        return unit, factor
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    return SUPPORTED_UNITS.get(code, (None, None))


def _segment_length(a: tuple[float, float], b: tuple[float, float], bulge: float) -> float:
    chord = math.hypot(b[0] - a[0], b[1] - a[1])
    if chord == 0 or abs(bulge) < 1e-12:
        return chord
    theta = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    return radius * abs(theta)


def _segment_area_correction(a: tuple[float, float], b: tuple[float, float],
                              bulge: float) -> float:
    chord = math.hypot(b[0] - a[0], b[1] - a[1])
    if chord == 0 or abs(bulge) < 1e-12:
        return 0.0
    theta = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    return 0.5 * radius * radius * (theta - math.sin(theta))


def _polyline_vertices(entity: Any) -> tuple[list[tuple[float, float, float]], bool]:
    if entity.dxftype() == "LWPOLYLINE":
        vertices = [(float(x), float(y), float(b)) for x, y, b in entity.get_points("xyb")]
        return vertices, bool(entity.closed)
    if entity.dxftype() == "POLYLINE":
        if entity.is_3d_polyline:
            return [], False
        vertices = []
        for vertex in entity.vertices:
            location = vertex.dxf.location
            vertices.append((float(location.x), float(location.y),
                             float(vertex.dxf.get("bulge", 0.0))))
        return vertices, bool(entity.is_closed)
    return [], False


def _polyline_geometry(entity: Any) -> tuple[float, float | None, bool]:
    vertices, closed = _polyline_vertices(entity)
    if len(vertices) < 2:
        return 0.0, None, False
    points = [(x, y) for x, y, _ in vertices]
    segment_count = len(points) if closed else len(points) - 1
    length = 0.0
    for index in range(segment_count):
        next_index = (index + 1) % len(points)
        length += _segment_length(points[index], points[next_index], vertices[index][2])
    if not closed:
        return length, None, True
    signed_area = sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    ) / 2.0
    signed_area += sum(
        _segment_area_correction(points[i], points[(i + 1) % len(points)], vertices[i][2])
        for i in range(len(points))
    )
    return length, abs(signed_area), True


def _arc_sweep(start_degrees: float, end_degrees: float) -> float:
    delta = end_degrees - start_degrees
    if abs(delta) >= 360.0 - 1e-9:
        return 2.0 * math.pi
    return math.radians(delta % 360.0)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_dxf_geometry(
    path: Path,
    *,
    layer_patterns: list[str] | None = None,
    source_unit_override: str | None = None,
) -> dict[str, Any]:
    """Measure supported model-space geometry and return entity-level evidence.

    Lengths and areas are planimetric (XY). Unit conversion is withheld when
    $INSUNITS is unitless/unknown and no explicit override is supplied.
    """
    path = Path(path)
    if path.suffix.lower() != ".dxf":
        raise DrawingGeometryError("Native geometry engine accepts DXF only; convert DWG first.")
    if not path.is_file() or path.stat().st_size == 0:
        raise DrawingGeometryError("DXF file is missing or empty.")
    try:
        doc = ezdxf.readfile(path)
    except (OSError, DXFStructureError) as exc:
        raise DrawingGeometryError(f"Unable to parse DXF: {exc}") from exc

    source_unit, to_meters = _unit_info(doc, source_unit_override)
    patterns = [item.strip().casefold() for item in (layer_patterns or []) if item.strip()]
    measurements: list[dict[str, Any]] = []
    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"entity_count": 0, "linear_length": 0.0, "closed_area": 0.0,
                 "block_counts": defaultdict(int), "entity_types": defaultdict(int)}
    )
    warnings: list[str] = []
    unsupported: dict[str, int] = defaultdict(int)
    seen_handles: set[str] = set()

    def add_measurement(entity: Any, kind: str, value: float, formula: str,
                        confidence: str = "exact_geometry", extra_warnings: list[str] | None = None):
        layer = str(entity.dxf.layer)
        handle = str(entity.dxf.handle)
        raw = _decimal(value)
        normalized = raw * to_meters if to_meters is not None else None
        raw_unit = (
            f"{source_unit}2" if kind == "area" and source_unit
            else source_unit or "unitless_or_unknown"
        )
        measurements.append({
            "entity_handle": handle,
            "entity_type": entity.dxftype(),
            "layer": layer,
            "measurement_kind": kind,
            "raw_value": raw,
            "source_unit": raw_unit,
            "normalized_value": normalized,
            "normalized_unit": ("m" if kind == "length" else "m2") if normalized is not None else None,
            "formula": formula,
            "confidence_class": confidence,
            "warnings": extra_warnings or [],
        })

    for entity in doc.modelspace():
        layer = str(entity.dxf.layer)
        if patterns and not any(fnmatch.fnmatchcase(layer.casefold(), pattern) for pattern in patterns):
            continue
        handle = str(entity.dxf.handle)
        if handle in seen_handles:
            warnings.append(f"DUPLICATE_HANDLE_SKIPPED:{handle}")
            continue
        seen_handles.add(handle)
        kind = entity.dxftype()
        bucket = totals[layer]
        bucket["entity_count"] += 1
        bucket["entity_types"][kind] += 1

        if kind == "LINE":
            start, end = entity.dxf.start, entity.dxf.end
            dx, dy = float(end.x - start.x), float(end.y - start.y)
            add_measurement(entity, "length", math.hypot(dx, dy), "sqrt((x2-x1)^2+(y2-y1)^2)",
                            extra_warnings=["Z_COORDINATE_IGNORED"] if abs(float(end.z-start.z)) > 1e-9 else [])
            bucket["linear_length"] += math.hypot(dx, dy)
        elif kind == "ARC":
            radius = float(entity.dxf.radius)
            value = radius * _arc_sweep(float(entity.dxf.start_angle), float(entity.dxf.end_angle))
            add_measurement(entity, "length", value, "radius * counterclockwise_sweep_radians")
            bucket["linear_length"] += value
        elif kind == "CIRCLE":
            radius = float(entity.dxf.radius)
            length, area = 2.0 * math.pi * radius, math.pi * radius * radius
            add_measurement(entity, "length", length, "2*pi*radius")
            add_measurement(entity, "area", area, "pi*radius^2")
            bucket["linear_length"] += length
            bucket["closed_area"] += area
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            length, area, supported = _polyline_geometry(entity)
            if not supported:
                unsupported[kind] += 1
                warnings.append(f"UNSUPPORTED_POLYLINE_GEOMETRY:{handle}")
                continue
            add_measurement(entity, "length", length, "sum(straight_chords_and_bulge_arc_lengths)")
            bucket["linear_length"] += length
            if area is not None:
                add_measurement(entity, "area", area, "shoelace_area_plus_signed_bulge_segment_corrections",
                                extra_warnings=["SELF_INTERSECTION_NOT_VALIDATED"])
                bucket["closed_area"] += area
        elif kind == "INSERT":
            block_name = str(entity.dxf.name)
            bucket["block_counts"][block_name] += 1
            measurements.append({
                "entity_handle": handle, "entity_type": kind, "layer": layer,
                "measurement_kind": "count", "block_name": block_name,
                "raw_value": Decimal(1), "source_unit": "count",
                "normalized_value": Decimal(1), "normalized_unit": "count",
                "formula": "one_modelspace_insert_equals_one_insert",
                "confidence_class": "block_count", "warnings": ["NESTED_BLOCKS_NOT_EXPANDED"],
            })
        elif kind in {"DIMENSION", "HATCH", "SPLINE", "ELLIPSE", "RAY", "XLINE"}:
            unsupported[kind] += 1
            if kind == "HATCH":
                warnings.append(f"HATCH_AREA_NOT_MEASURED:{handle}")
            elif kind == "DIMENSION":
                warnings.append(f"DIMENSION_ANNOTATION_NOT_COUNTED_AS_GEOMETRY:{handle}")
        else:
            unsupported[kind] += 1

    if source_unit is None:
        warnings.append("UNIT_UNKNOWN: normalized metric quantities withheld; confirm drawing units.")
    if unsupported:
        warnings.append("UNSUPPORTED_ENTITY_TYPES:" + ",".join(
            f"{name}={count}" for name, count in sorted(unsupported.items())
        ))
    if any(bucket["block_counts"] for bucket in totals.values()):
        warnings.append("BLOCK_COUNTS_ARE_TOP_LEVEL_MODELSPACE_INSERTS_ONLY.")
    has_xrefs = False
    for block_layout in doc.blocks:
        if block_layout.name.startswith("*"):
            continue
        flags = int(block_layout.block.dxf.get("flags", 0))
        if flags & 0xC:
            has_xrefs = True
            break
    if has_xrefs:
        warnings.append("XREF_BLOCKS_PRESENT: external references are not expanded.")

    layers = []
    for name, bucket in sorted(totals.items()):
        raw_length = _decimal(bucket["linear_length"])
        raw_area = _decimal(bucket["closed_area"])
        layers.append({
            "layer": name,
            "entity_count": bucket["entity_count"],
            "entity_types": dict(bucket["entity_types"]),
            "linear_length_raw": raw_length,
            "linear_length_unit": source_unit or "unitless_or_unknown",
            "linear_length_m": raw_length * to_meters if to_meters is not None else None,
            "closed_area_raw": raw_area,
            "closed_area_unit": f"{source_unit}2" if source_unit else "unitless_or_unknown",
            "closed_area_m2": raw_area * to_meters * to_meters if to_meters is not None else None,
            "block_counts": dict(bucket["block_counts"]),
        })
    return {
        "filename": path.name,
        "drawing_sha256": _hash_file(path),
        "dxf_version": doc.dxfversion,
        "scope": "modelspace_xy_only",
        "source_unit": source_unit,
        "normalized_length_unit": "m" if to_meters is not None else None,
        "normalized_area_unit": "m2" if to_meters is not None else None,
        "unit_status": "confirmed" if to_meters is not None else "requires_confirmation",
        "layers": layers,
        "measurements": measurements,
        "measurement_count": len(measurements),
        "warnings": warnings,
    }
