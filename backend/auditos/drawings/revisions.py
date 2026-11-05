from __future__ import annotations

from decimal import Decimal
from typing import Any


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
