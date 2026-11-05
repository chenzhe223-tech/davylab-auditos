from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import ezdxf

from auditos.drawings.geometry import UNIT_ALIASES, UNIT_FACTORS


def compare_drawing_revision_reports(
    base_report: dict[str, Any], target_report: dict[str, Any]
) -> dict[str, Any]:
    for report, label in ((base_report, "Base"), (target_report, "Target")):
        has_dimensions = any(
            item.get("measurement_kind") in {"length", "area"}
            for item in report.get("measurements", [])
        )
        if has_dimensions and not report.get("source_unit"):
            raise ValueError(f"{label} drawing unit is unknown; confirm it first.")

    base_layers = {item["layer"]: item for item in base_report.get("layers", [])}
    target_layers = {item["layer"]: item for item in target_report.get("layers", [])}
    names = sorted(set(base_layers) | set(target_layers), key=str.casefold)
    changes = []
    totals = {
        "base_linear_length_m": Decimal(0),
        "target_linear_length_m": Decimal(0),
        "delta_linear_length_m": Decimal(0),
        "base_closed_area_m2": Decimal(0),
        "target_closed_area_m2": Decimal(0),
        "delta_closed_area_m2": Decimal(0),
        "base_entity_count": 0,
        "target_entity_count": 0,
        "delta_entity_count": 0,
    }

    for name in names:
        base = base_layers.get(name, {})
        target = target_layers.get(name, {})
        base_length = Decimal(str(base.get("linear_length_m") or 0))
        target_length = Decimal(str(target.get("linear_length_m") or 0))
        base_area = Decimal(str(base.get("closed_area_m2") or 0))
        target_area = Decimal(str(target.get("closed_area_m2") or 0))
        base_count = int(base.get("entity_count", 0))
        target_count = int(target.get("entity_count", 0))
        base_blocks = base.get("block_counts", {})
        target_blocks = target.get("block_counts", {})
        block_deltas = {
            block: int(target_blocks.get(block, 0)) - int(base_blocks.get(block, 0))
            for block in sorted(set(base_blocks) | set(target_blocks), key=str.casefold)
            if int(target_blocks.get(block, 0)) != int(base_blocks.get(block, 0))
        }
        delta_length = target_length - base_length
        delta_area = target_area - base_area
        delta_count = target_count - base_count
        if base_count == 0 and target_count > 0:
            status = "added"
        elif target_count == 0 and base_count > 0:
            status = "removed"
        elif delta_length or delta_area or delta_count or block_deltas:
            status = "changed"
        else:
            status = "unchanged"
        changes.append({
            "layer": name, "status": status,
            "base_linear_length_m": base_length,
            "target_linear_length_m": target_length,
            "delta_linear_length_m": delta_length,
            "base_closed_area_m2": base_area,
            "target_closed_area_m2": target_area,
            "delta_closed_area_m2": delta_area,
            "base_entity_count": base_count,
            "target_entity_count": target_count,
            "delta_entity_count": delta_count,
            "base_block_counts": base_blocks,
            "target_block_counts": target_blocks,
            "delta_block_counts": block_deltas,
        })
        totals["base_linear_length_m"] += base_length
        totals["target_linear_length_m"] += target_length
        totals["delta_linear_length_m"] += delta_length
        totals["base_closed_area_m2"] += base_area
        totals["target_closed_area_m2"] += target_area
        totals["delta_closed_area_m2"] += delta_area
        totals["base_entity_count"] += base_count
        totals["target_entity_count"] += target_count
        totals["delta_entity_count"] += delta_count

    return {
        "comparison_type": "native_dxf_layer_revision_delta",
        "base_revision": base_report.get("drawing_revision"),
        "target_revision": target_report.get("drawing_revision"),
        "base_drawing_sha256": base_report.get("drawing_sha256"),
        "target_drawing_sha256": target_report.get("drawing_sha256"),
        "base_source_unit": base_report.get("source_unit"),
        "target_source_unit": target_report.get("source_unit"),
        "totals": totals,
        "layers": changes,
        "summary": {
            "added_layers": sum(item["status"] == "added" for item in changes),
            "removed_layers": sum(item["status"] == "removed" for item in changes),
            "changed_layers": sum(item["status"] == "changed" for item in changes),
            "unchanged_layers": sum(item["status"] == "unchanged" for item in changes),
        },
        "limitations": [
            "Layer-level delta only; no automatic entity identity matching across revisions.",
            "A layer rename appears as removal plus addition.",
            "Only supported model-space geometry is compared.",
            "Human review is required before commercial use.",
        ],
    }



def compare_dxf_entity_revisions(
    base_path: Path,
    target_path: Path,
    base_unit: str | None,
    target_unit: str | None,
    *,
    tolerance_m: Decimal = Decimal("0.0001"),
    max_entities: int = 50000,
    max_changes: int = 5000,
) -> dict[str, Any]:
    """Match supported model-space entities by normalized geometry signatures."""
    if not base_unit or not target_unit:
        raise ValueError("Entity-level revision matching requires confirmed units.")
    base_alias = UNIT_ALIASES.get(base_unit.lower(), base_unit.lower())
    target_alias = UNIT_ALIASES.get(target_unit.lower(), target_unit.lower())
    base_factor = UNIT_FACTORS.get(base_alias)
    target_factor = UNIT_FACTORS.get(target_alias)
    if base_factor is None or target_factor is None:
        raise ValueError("Unsupported drawing unit for entity-level revision matching.")

    def quantize(value: float, factor: Decimal) -> int:
        normalized = Decimal(str(value)) * factor
        return int((normalized / tolerance_m).to_integral_value())

    def signature(entity: Any, factor: Decimal) -> str | None:
        kind = entity.dxftype()
        if kind == "LINE":
            points = [
                tuple(quantize(float(value), factor) for value in entity.dxf.start),
                tuple(quantize(float(value), factor) for value in entity.dxf.end),
            ]
            geometry = {"type": kind, "points": sorted(points)}
        elif kind in {"ARC", "CIRCLE"}:
            center = tuple(quantize(float(value), factor) for value in entity.dxf.center)
            extrusion = tuple(round(float(value), 6) for value in entity.dxf.get("extrusion", (0, 0, 1)))
            geometry = {
                "type": kind,
                "center": center,
                "radius": quantize(float(entity.dxf.radius), factor),
                "extrusion": extrusion,
            }
            if kind == "ARC":
                geometry["start_angle"] = round(float(entity.dxf.start_angle), 5)
                geometry["end_angle"] = round(float(entity.dxf.end_angle), 5)
        elif kind == "LWPOLYLINE":
            points = [
                (
                    quantize(float(x), factor),
                    quantize(float(y), factor),
                    round(float(bulge), 8),
                )
                for x, y, bulge in entity.get_points("xyb")
            ]
            geometry = {
                "type": kind,
                "points": points,
                "closed": bool(entity.closed),
                "elevation": quantize(float(entity.dxf.elevation), factor),
                "extrusion": tuple(
                    round(float(value), 6)
                    for value in entity.dxf.get("extrusion", (0, 0, 1))
                ),
            }
        elif kind == "POLYLINE":
            if entity.is_3d_polyline:
                return None
            points = [
                (
                    quantize(float(vertex.dxf.location.x), factor),
                    quantize(float(vertex.dxf.location.y), factor),
                    quantize(float(vertex.dxf.location.z), factor),
                    round(float(vertex.dxf.get("bulge", 0.0)), 8),
                )
                for vertex in entity.vertices
            ]
            geometry = {"type": kind, "points": points, "closed": bool(entity.is_closed)}
        elif kind == "INSERT":
            geometry = {
                "type": kind,
                "name": str(entity.dxf.name),
                "insert": tuple(quantize(float(value), factor) for value in entity.dxf.insert),
                "rotation": round(float(entity.dxf.rotation) % 360, 5),
                "scale": tuple(
                    round(float(entity.dxf.get(key, 1.0)), 6)
                    for key in ("xscale", "yscale", "zscale")
                ),
            }
        else:
            return None
        return json.dumps(geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def read_entities(path: Path, factor: Decimal) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        doc = ezdxf.readfile(path)
        entities: dict[str, dict[str, Any]] = {}
        ignored: dict[str, int] = defaultdict(int)
        for entity in doc.modelspace():
            kind = entity.dxftype()
            entity_signature = signature(entity, factor)
            if entity_signature is None:
                ignored[kind] += 1
                continue
            handle = str(entity.dxf.handle).upper()
            entities[handle] = {
                "handle": handle,
                "layer": str(entity.dxf.layer),
                "entity_type": kind,
                "signature": entity_signature,
                "signature_hash": hashlib.sha256(entity_signature.encode()).hexdigest()[:16],
            }
        if len(entities) > max_entities:
            raise ValueError(f"Drawing exceeds entity comparison limit ({max_entities}).")
        return entities, dict(ignored)

    base_entities, base_ignored = read_entities(Path(base_path), base_factor)
    target_entities, target_ignored = read_entities(Path(target_path), target_factor)
    base_layers: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    target_layers: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for handle, entity in base_entities.items():
        base_layers[entity["layer"]][handle] = entity
    for handle, entity in target_entities.items():
        target_layers[entity["layer"]][handle] = entity

    changes = []
    counts = {
        "unchanged_same_handle": 0,
        "unchanged_geometry_handle_changed": 0,
        "modified_same_handle": 0,
        "added": 0,
        "removed": 0,
    }
    for layer in sorted(set(base_layers) | set(target_layers), key=str.casefold):
        old = dict(base_layers.get(layer, {}))
        new = dict(target_layers.get(layer, {}))
        for handle in sorted(set(old) & set(new)):
            before, after = old.pop(handle), new.pop(handle)
            if before["signature"] == after["signature"]:
                counts["unchanged_same_handle"] += 1
            else:
                counts["modified_same_handle"] += 1
                changes.append({
                    "layer": layer, "status": "modified_same_handle",
                    "base_handle": handle, "target_handle": handle,
                    "entity_type": after["entity_type"],
                    "base_geometry_hash": before["signature_hash"],
                    "target_geometry_hash": after["signature_hash"],
                })

        old_by_signature: dict[str, list[dict[str, Any]]] = defaultdict(list)
        new_by_signature: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entity in old.values():
            old_by_signature[entity["signature"]].append(entity)
        for entity in new.values():
            new_by_signature[entity["signature"]].append(entity)
        for entity_signature in set(old_by_signature) & set(new_by_signature):
            old_matches = sorted(old_by_signature[entity_signature], key=lambda item: item["handle"])
            new_matches = sorted(new_by_signature[entity_signature], key=lambda item: item["handle"])
            pair_count = min(len(old_matches), len(new_matches))
            counts["unchanged_geometry_handle_changed"] += pair_count
            for before, after in zip(old_matches[:pair_count], new_matches[:pair_count], strict=False):
                changes.append({
                    "layer": layer, "status": "unchanged_geometry_handle_changed",
                    "base_handle": before["handle"], "target_handle": after["handle"],
                    "entity_type": after["entity_type"],
                    "geometry_hash": after["signature_hash"],
                })
            for before in old_matches[pair_count:]:
                old.pop(before["handle"], None)
            for after in new_matches[pair_count:]:
                new.pop(after["handle"], None)

        for handle, entity in old.items():
            counts["removed"] += 1
            changes.append({
                "layer": layer, "status": "removed",
                "base_handle": handle, "target_handle": None,
                "entity_type": entity["entity_type"],
                "base_geometry_hash": entity["signature_hash"],
            })
        for handle, entity in new.items():
            counts["added"] += 1
            changes.append({
                "layer": layer, "status": "added",
                "base_handle": None, "target_handle": handle,
                "entity_type": entity["entity_type"],
                "target_geometry_hash": entity["signature_hash"],
            })

    return {
        "matching_method": "normalized_geometry_signature_with_handle_fallback",
        "tolerance_m": str(tolerance_m),
        "summary": counts,
        "total_changed_entities": (
            counts["modified_same_handle"] + counts["added"] + counts["removed"]
        ),
        "ignored_entity_types": {
            "base": base_ignored,
            "target": target_ignored,
        },
        "changes": changes[:max_changes],
        "truncated": len(changes) > max_changes,
        "limitations": [
            "Only supported model-space LINE, ARC, CIRCLE, LWPOLYLINE, 2D POLYLINE and INSERT entities are matched.",
            "Layer renames are represented as removals and additions.",
            "Block definition internals, Xrefs, layout entities and 3D polylines are not expanded.",
            "Geometry signature matching is tolerance-based; close but distinct entities may require manual review.",
        ],
    }
