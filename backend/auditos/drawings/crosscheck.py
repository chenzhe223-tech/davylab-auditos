"""Conservative comparison of native geometry with DXF Civil preliminary takeoff."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

LINEAR_ENTITY_TYPES = {"LINE", "ARC", "LWPOLYLINE", "POLYLINE"}
POLYLINE_ENTITY_TYPES = {"LWPOLYLINE", "POLYLINE"}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal(0)


def _compare_metric(
    name: str,
    native_value: Decimal,
    external_value: Decimal,
    *,
    comparable: bool = True,
    absolute_tolerance: Decimal = Decimal("0.0002"),
) -> dict[str, Any]:
    if not comparable:
        return {
            "metric": name,
            "native_value": str(native_value),
            "external_value": str(external_value),
            "difference": None,
            "tolerance": str(absolute_tolerance),
            "status": "not_comparable_upstream_geometry_limit",
        }
    difference = abs(native_value - external_value)
    tolerance = max(
        absolute_tolerance,
        max(abs(native_value), abs(external_value)) * Decimal("0.0001"),
    )
    return {
        "metric": name,
        "native_value": str(native_value),
        "external_value": str(external_value),
        "difference": str(difference),
        "tolerance": str(tolerance),
        "status": "match" if difference <= tolerance else "difference",
    }


def compare_dxf_civil_takeoff(
    native_report: dict[str, Any], external_report: dict[str, Any]
) -> dict[str, Any]:
    """Compare only metrics with known compatible semantics.

    DXF Civil's takeoff ignores polyline bulges and labels HATCH bbox areas as
    proxies. Those metrics are explicitly withheld from comparison.
    """
    native_measurements = native_report.get("measurements", [])
    native_layers = native_report.get("layers", [])
    external_layers = {
        item.get("layer"): item
        for item in external_report.get("layers", [])
        if item.get("layer") is not None
    }
    native_layer_names = {item.get("layer") for item in native_layers}
    all_layers = sorted(native_layer_names | set(external_layers), key=lambda item: str(item).casefold())
    layer_results = []

    for layer in all_layers:
        rows = [item for item in native_measurements if item.get("layer") == layer]
        external = external_layers.get(layer)
        has_bulge = any(bool(item.get("has_bulge")) for item in rows)
        native_linear = sum(
            (_decimal(item.get("raw_value")) for item in rows
             if item.get("measurement_kind") == "length"
             and item.get("entity_type") in LINEAR_ENTITY_TYPES),
            Decimal(0),
        )
        native_polyline_area = sum(
            (_decimal(item.get("raw_value")) for item in rows
             if item.get("measurement_kind") == "area"
             and item.get("entity_type") in POLYLINE_ENTITY_TYPES),
            Decimal(0),
        )
        native_circle_area = sum(
            (_decimal(item.get("raw_value")) for item in rows
             if item.get("measurement_kind") == "area"
             and item.get("entity_type") == "CIRCLE"),
            Decimal(0),
        )
        native_block_count = sum(
            (_decimal(value) for item in native_layers if item.get("layer") == layer
             for value in item.get("block_counts", {}).values()),
            Decimal(0),
        )

        if external is None:
            metrics = [{
                "metric": name,
                "native_value": str(value),
                "external_value": None,
                "difference": None,
                "tolerance": None,
                "status": "missing_external_layer",
            } for name, value in [
                ("linear_length", native_linear),
                ("closed_polyline_area", native_polyline_area),
                ("circle_area", native_circle_area),
                ("block_insert_count", native_block_count),
            ]]
        else:
            external_linear = _decimal(external.get("total_length"))
            external_polyline_area = _decimal(external.get("closed_polyline_area"))
            external_circle_area = _decimal(external.get("circle_total_area"))
            external_block_count = sum(
                (_decimal(value) for value in external.get("block_inserts", {}).values()),
                Decimal(0),
            )
            metrics = [
                _compare_metric(
                    "linear_length", native_linear, external_linear,
                    comparable=not has_bulge,
                ),
                _compare_metric(
                    "closed_polyline_area", native_polyline_area, external_polyline_area,
                    comparable=not has_bulge,
                ),
                _compare_metric("circle_area", native_circle_area, external_circle_area),
                _compare_metric("block_insert_count", native_block_count, external_block_count),
            ]
        layer_results.append({
            "layer": layer,
            "has_bulge_segments": has_bulge,
            "metrics": metrics,
        })

    statuses = [metric["status"] for layer in layer_results for metric in layer["metrics"]]
    if "difference" in statuses:
        overall = "differences_found"
    elif statuses and all(status == "match" for status in statuses):
        overall = "match"
    elif statuses:
        overall = "partial_or_not_comparable"
    else:
        overall = "no_comparable_metrics"
    return {
        "external_engine": "dxf_civil",
        "overall_status": overall,
        "layers": layer_results,
        "rules": [
            "No averaging or automatic replacement of native values.",
            "Polyline length/area is not compared when bulge segments are present.",
            "HATCH bbox proxy area is excluded from all comparisons.",
            "All differences require drawing-level human review.",
        ],
    }



def compare_cad_ai_takeoff(
    native_report: dict[str, Any], external_report: dict[str, Any]
) -> dict[str, Any]:
    """Compare CAD AI Agent's LINE/LWPOLYLINE and symbol candidates."""
    rows = native_report.get("measurements", [])
    metrics = []
    has_inserts = any(item.get("entity_type") == "INSERT" for item in rows)
    for item in external_report.get("items", []):
        category = item.get("category")
        if category == "linear" and item.get("source_layer"):
            layer = item["source_layer"]
            native_value = sum(
                (_decimal(row.get("raw_value")) for row in rows
                 if row.get("layer") == layer
                 and row.get("entity_type") in {"LINE", "LWPOLYLINE"}
                 and row.get("measurement_kind") == "length"),
                Decimal(0),
            )
            has_bulge = any(
                bool(row.get("has_bulge")) for row in rows
                if row.get("layer") == layer and row.get("entity_type") == "LWPOLYLINE"
            )
            metrics.append({
                "layer": layer,
                **_compare_metric(
                    "line_and_lwpolyline_length",
                    native_value,
                    _decimal(item.get("quantity")),
                    comparable=not has_bulge and not has_inserts,
                    absolute_tolerance=Decimal("0.005"),
                ),
            })
        elif category == "symbol":
            block_name = item.get("name")
            native_value = sum(
                (_decimal(row.get("raw_value")) for row in rows
                 if row.get("entity_type") == "INSERT"
                 and row.get("block_name") == block_name
                 and row.get("measurement_kind") == "count"),
                Decimal(0),
            )
            metrics.append({
                "block_name": block_name,
                **_compare_metric(
                    "block_symbol_count",
                    native_value,
                    _decimal(item.get("quantity")),
                    comparable=True,
                    absolute_tolerance=Decimal(0),
                ),
            })

    statuses = [item["status"] for item in metrics]
    overall = (
        "differences_found" if "difference" in statuses else
        "match" if statuses and all(status == "match" for status in statuses) else
        "partial_or_not_comparable" if statuses else
        "no_comparable_metrics"
    )
    return {
        "external_engine": "cad_ai_agent",
        "overall_status": overall,
        "metrics": metrics,
        "rules": [
            "Only LINE/LWPOLYLINE linear candidates and INSERT symbol counts are compared.",
            "Linear comparison is withheld when bulge segments or INSERT expansion may affect totals.",
            "Zone-derived areas and non-comparable categories remain candidate-only.",
            "CAD AI Agent linear values are rounded to two decimals; tolerance is 0.005 drawing units.",
        ],
    }


def compare_dwg_reader_csv(
    native_report: dict[str, Any], csv_path: str | Path
) -> dict[str, Any]:
    """Compare only per-layer LINE lengths from DWG Reader's sectioned CSV."""
    path = Path(csv_path)
    if not path.is_file():
        return {
            "external_engine": "dwg_reader",
            "overall_status": "candidate_output_missing",
            "metrics": [],
        }
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    external_layers: dict[str, Decimal] = {}
    for index, row in enumerate(rows):
        if not row or row[0].strip() != "[图层几何量]":
            continue
        if index + 1 >= len(rows):
            continue
        headers = rows[index + 1]
        for values in rows[index + 2:]:
            if not values or (values[0].startswith("[") and values[0].endswith("]")):
                break
            record = dict(zip(headers, values))
            layer = record.get("图层")
            if layer:
                external_layers[layer] = _decimal(record.get("直线总长"))
        break

    native_rows = native_report.get("measurements", [])
    has_inserts = any(item.get("entity_type") == "INSERT" for item in native_rows)
    native_layers = sorted(
        {item.get("layer") for item in native_rows if item.get("entity_type") == "LINE"},
        key=lambda item: str(item).casefold(),
    )
    metrics = []
    for layer in sorted(set(native_layers) | set(external_layers), key=str.casefold):
        native_value = sum(
            (_decimal(item.get("raw_value")) for item in native_rows
             if item.get("layer") == layer and item.get("entity_type") == "LINE"
             and item.get("measurement_kind") == "length"),
            Decimal(0),
        )
        if layer not in external_layers:
            metrics.append({
                "layer": layer,
                "metric": "line_length",
                "native_value": str(native_value),
                "external_value": None,
                "difference": None,
                "tolerance": None,
                "status": "missing_external_layer",
            })
        else:
            metrics.append({
                "layer": layer,
                **_compare_metric(
                    "line_length",
                    native_value,
                    external_layers[layer],
                    comparable=not has_inserts,
                    absolute_tolerance=Decimal("0.0005"),
                ),
            })

    statuses = [item["status"] for item in metrics]
    overall = (
        "differences_found" if "difference" in statuses else
        "match" if statuses and all(status == "match" for status in statuses) else
        "partial_or_not_comparable" if statuses else
        "no_comparable_metrics"
    )
    return {
        "external_engine": "dwg_reader",
        "overall_status": overall,
        "metrics": metrics,
        "rules": [
            "Only LINE lengths are compared; polyline length and component groups are not normalized.",
            "Comparison is withheld when INSERT entities may cause different block expansion scope.",
            "DWG Reader values are rounded to three decimals in drawing units; tolerance is 0.0005.",
        ],
    }
