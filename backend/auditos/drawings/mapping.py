"""Editable installation layer-mapping templates.

These are discovery hints, not statutory measurement rules or automatic BOQ
classification. A project/company mapping must be reviewed before use.
"""
from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel


class LayerMappingRule(BaseModel):
    rule_id: str
    discipline: str
    item_name: str
    measurement_kind: str
    unit: str
    layer_patterns: list[str]
    entity_types: list[str]
    notes: str
    status: str = "template_needs_confirmation"
    source: str = "DAVYLAB_TEMPLATE_NOT_A_STATUTORY_STANDARD"
    version: int | None = None


RULES = [
    LayerMappingRule(
        rule_id="electrical.cable_tray.length",
        discipline="electrical",
        item_name="电缆桥架中心线长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*电缆桥架*", "*桥架*", "*E-TRAY*", "*TRAY*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="仅按图层几何提取平面长度；须确认中心线、立管、弯通、三通及余量规则。",
    ),
    LayerMappingRule(
        rule_id="electrical.conduit.length",
        discipline="electrical",
        item_name="电线管/穿线管长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*电线管*", "*穿线管*", "*E-CONDUIT*", "*CONDUIT*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="须确认管径、敷设方式、竖向段和预留长度。",
    ),
    LayerMappingRule(
        rule_id="electrical.panel.count",
        discipline="electrical",
        item_name="配电箱/柜数量候选",
        measurement_kind="count",
        unit="台",
        layer_patterns=["*配电箱*", "*配电柜*", "*E-PANEL*", "*PANEL*"],
        entity_types=["INSERT"],
        notes="只统计模型空间顶层块插入；需核对块名、动态块和重复参照。",
    ),
    LayerMappingRule(
        rule_id="electrical.light.count",
        discipline="electrical",
        item_name="灯具数量候选",
        measurement_kind="count",
        unit="套",
        layer_patterns=["*灯具*", "*E-LIGHT*", "*LIGHT-FIXTURE*"],
        entity_types=["INSERT"],
        notes="只统计模型空间顶层块插入；需核对灯具类型表和块属性。",
    ),
    LayerMappingRule(
        rule_id="plumbing.water_supply.length",
        discipline="plumbing",
        item_name="给水管道长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*给水管*", "*冷水管*", "*热水管*", "*P-CW*", "*P-HW*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="须区分系统、材质、管径，并确认立管/支管与连接件计量规则。",
    ),
    LayerMappingRule(
        rule_id="plumbing.drainage.length",
        discipline="plumbing",
        item_name="排水管道长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*排水管*", "*污水管*", "*废水管*", "*P-DRAIN*", "*P-SAN*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="须区分重力流/压力流、管径、坡度和立管系统。",
    ),
    LayerMappingRule(
        rule_id="plumbing.pump.count",
        discipline="plumbing",
        item_name="水泵设备数量候选",
        measurement_kind="count",
        unit="台",
        layer_patterns=["*水泵*", "*P-PUMP*"],
        entity_types=["INSERT"],
        notes="须关联设备表、规格型号和备用设备状态。",
    ),
    LayerMappingRule(
        rule_id="hvac.duct_centerline.length",
        discipline="hvac",
        item_name="风管中心线长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*风管中心线*", "*M-DUCT-CL*", "*DUCT-CL*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="仅接受明确标识为中心线的图层；风管展开面积需另行按截面和周长计算。",
    ),
    LayerMappingRule(
        rule_id="hvac.fan.count",
        discipline="hvac",
        item_name="风机/空调设备数量候选",
        measurement_kind="count",
        unit="台",
        layer_patterns=["*风机*", "*M-FAN*", "*AHU*"],
        entity_types=["INSERT"],
        notes="须关联设备表并核对设备类型、规格及备用状态。",
    ),
    LayerMappingRule(
        rule_id="fire_protection.fire_pipe.length",
        discipline="fire_protection",
        item_name="消防管道长度候选",
        measurement_kind="length",
        unit="m",
        layer_patterns=["*消防管*", "*喷淋管*", "*消火栓管*", "*FP-PIPE*", "*FP-SPRINKLER-PIPE*"],
        entity_types=["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
        notes="须按消防系统、管径、材质和标高分组，核对竖向段及管件规则。",
    ),
    LayerMappingRule(
        rule_id="fire_protection.sprinkler_head.count",
        discipline="fire_protection",
        item_name="喷头数量候选",
        measurement_kind="count",
        unit="只",
        layer_patterns=["*喷头*", "*FP-HEAD*", "*SPRINKLER-HEAD*"],
        entity_types=["INSERT"],
        notes="须核对喷头类型、保护范围、重复块和图例。",
    ),
]


def get_mapping_templates(discipline: str | None = None) -> list[dict[str, Any]]:
    selected = [
        rule for rule in RULES
        if discipline is None or discipline == "all" or rule.discipline == discipline
    ]
    return [rule.model_dump(mode="json") for rule in selected]


def suggest_layer_mappings(
    layer_names: list[str],
    discipline: str = "all",
    custom_rules: list[dict[str, Any]] | None = None,
    layer_entity_types: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    suggestions = []
    project_rules = [LayerMappingRule.model_validate(rule) for rule in (custom_rules or [])]
    for layer in sorted(set(layer_names), key=str.casefold):
        normalized = layer.casefold()
        matches = [
            rule for rule in project_rules
            if (discipline == "all" or rule.discipline == discipline)
            and any(fnmatch.fnmatchcase(normalized, pattern.casefold())
                    for pattern in rule.layer_patterns)
        ]
        if not matches:
            matches = [
                rule for rule in RULES
                if (discipline == "all" or rule.discipline == discipline)
                and any(fnmatch.fnmatchcase(normalized, pattern.casefold())
                        for pattern in rule.layer_patterns)
            ]
        if not matches:
            suggestions.append({
                "layer": layer,
                "status": "unmapped",
                "candidates": [],
                "notice": "未命中模板；需由项目人员建立自定义图层映射。",
            })
            continue
        actual_types = set((layer_entity_types or {}).get(layer, []))
        candidates = []
        mismatched = False
        for rule in matches:
            expected_types = set(rule.entity_types)
            if not actual_types:
                type_check = "not_checked"
            elif not expected_types or actual_types.intersection(expected_types):
                type_check = "compatible"
            else:
                type_check = "entity_type_mismatch"
                mismatched = True
            candidates.append({
                **rule.model_dump(mode="json"),
                "entity_type_check": type_check,
                "actual_entity_types": sorted(actual_types),
            })
        overall_status = (
            "ambiguous_needs_manual_mapping" if len(matches) > 1 else
            "entity_type_mismatch_needs_review" if mismatched else
            "candidate_needs_confirmation"
        )
        suggestions.append({
            "layer": layer,
            "status": overall_status,
            "mapping_source": matches[0].source,
            "candidates": candidates,
            "notice": (
                "存在多个候选映射，禁止自动归类。"
                if len(matches) > 1 else
                "图层内实体类型与规则预期不符，需检查图层语义或映射。"
                if mismatched else
                "仅为图层语义候选；确认专业、系统、构件及计量规则后方可采用。"
            ),
        })
    return suggestions
