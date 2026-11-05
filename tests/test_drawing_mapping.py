from auditos.domain.models import ProjectCreate
from auditos.drawings.mapping import (
    build_mapped_takeoff_candidates,
    get_mapping_templates,
    suggest_layer_mappings,
)
from auditos.storage.repository import ProjectRepository


def test_chinese_installation_layer_mapping_is_suggestion_only():
    result = suggest_layer_mappings(["电缆桥架-主干", "给水管-立管"], "all")
    by_layer = {item["layer"]: item for item in result}
    assert by_layer["给水管-立管"]["status"] == "candidate_needs_confirmation"
    assert by_layer["给水管-立管"]["candidates"][0]["rule_id"] == "plumbing.water_supply.length"
    assert by_layer["电缆桥架-主干"]["candidates"][0]["rule_id"] == "electrical.cable_tray.length"


def test_ambiguous_and_unmapped_layers_are_not_auto_classified():
    result = suggest_layer_mappings(["给水管喷淋管", "CUSTOM-LAYER"], "all")
    by_layer = {item["layer"]: item for item in result}
    assert by_layer["给水管喷淋管"]["status"] == "ambiguous_needs_manual_mapping"
    assert by_layer["CUSTOM-LAYER"]["status"] == "unmapped"
    assert by_layer["CUSTOM-LAYER"]["candidates"] == []


def test_discipline_filter_limits_mapping_templates():
    rules = get_mapping_templates("electrical")
    assert rules
    assert {item["discipline"] for item in rules} == {"electrical"}



def test_project_mapping_is_versioned_and_overrides_template(tmp_path):
    repository = ProjectRepository(tmp_path / "mapping.sqlite3")
    project = repository.create_project(ProjectCreate(name="Mapping test"))
    custom_rule = {
        "rule_id": "project.water.pipe",
        "discipline": "plumbing",
        "item_name": "项目自定义给水管",
        "measurement_kind": "length",
        "unit": "m",
        "layer_patterns": ["*给水管*"],
        "entity_types": ["LINE", "LWPOLYLINE"],
        "notes": "按项目约定复核立管和管件。",
    }

    first = repository.save_project_layer_mapping(project.id, custom_rule)
    second = repository.save_project_layer_mapping(project.id, custom_rule)

    assert first["version"] == 1
    assert second["version"] == 2
    saved = repository.list_project_layer_mappings(project.id)
    history = repository.list_project_layer_mapping_history(project.id, "project.water.pipe")
    result = suggest_layer_mappings(["给水管-立管"], "plumbing", saved)[0]

    assert [item["version"] for item in history] == [2, 1]
    assert result["mapping_source"] == "project_custom"
    assert result["candidates"][0]["item_name"] == "项目自定义给水管"
    assert result["candidates"][0]["version"] == 2



def test_mapping_checks_actual_entity_types_before_acceptance():
    result = suggest_layer_mappings(
        ["给水管", "给水管-文字"],
        "plumbing",
        layer_entity_types={
            "给水管": ["LINE", "LWPOLYLINE"],
            "给水管-文字": ["TEXT", "MTEXT"],
        },
    )
    by_layer = {item["layer"]: item for item in result}

    assert by_layer["给水管"]["status"] == "candidate_needs_confirmation"
    assert by_layer["给水管"]["candidates"][0]["entity_type_check"] == "compatible"
    assert by_layer["给水管-文字"]["status"] == "entity_type_mismatch_needs_review"



def test_mapped_linear_candidate_aggregates_only_entity_evidence():
    suggestions = suggest_layer_mappings(
        ["给水管"], "plumbing", layer_entity_types={"给水管": ["LINE", "TEXT"]}
    )
    report = {
        "mapping_suggestions": suggestions,
        "measurements": [
            {
                "layer": "给水管", "measurement_kind": "length", "entity_type": "LINE",
                "entity_handle": "A1", "normalized_value": "1.25",
            },
            {
                "layer": "给水管", "measurement_kind": "length", "entity_type": "LINE",
                "entity_handle": "A2", "normalized_value": "0.75",
            },
            {
                "layer": "给水管", "measurement_kind": "count", "entity_type": "INSERT",
                "entity_handle": "B1", "normalized_value": "1",
            },
        ],
    }

    candidate = build_mapped_takeoff_candidates(report)[0]

    assert candidate["status"] == "candidate_for_human_review"
    assert candidate["quantity"] == 2
    assert candidate["unit"] == "m"
    assert candidate["evidence_handles"] == ["A1", "A2"]
    assert candidate["requires_human_review"] is True


def test_unknown_units_and_ambiguous_mappings_withhold_candidate_quantity():
    unknown_unit = {
        "mapping_suggestions": suggest_layer_mappings(
            ["给水管"], "plumbing", layer_entity_types={"给水管": ["LINE"]}
        ),
        "measurements": [{
            "layer": "给水管", "measurement_kind": "length", "entity_type": "LINE",
            "entity_handle": "A1", "normalized_value": None,
        }],
    }
    ambiguous = {
        "mapping_suggestions": suggest_layer_mappings(
            ["给水管喷淋管"], "all", layer_entity_types={"给水管喷淋管": ["LINE"]}
        ),
        "measurements": [],
    }

    unknown_result = build_mapped_takeoff_candidates(unknown_unit)[0]
    ambiguous_result = build_mapped_takeoff_candidates(ambiguous)[0]

    assert unknown_result["status"] == "unit_confirmation_required"
    assert unknown_result["quantity"] is None
    assert ambiguous_result["status"] == "ambiguous_needs_manual_mapping"
    assert ambiguous_result["quantity"] is None


def test_block_count_mapping_sums_insert_evidence():
    suggestions = suggest_layer_mappings(
        ["配电箱"], "electrical", layer_entity_types={"配电箱": ["INSERT"]}
    )
    report = {
        "mapping_suggestions": suggestions,
        "measurements": [
            {
                "layer": "配电箱", "measurement_kind": "count", "entity_type": "INSERT",
                "entity_handle": "C1", "normalized_value": "1",
            },
            {
                "layer": "配电箱", "measurement_kind": "count", "entity_type": "INSERT",
                "entity_handle": "C2", "normalized_value": "1",
            },
        ],
    }

    candidate = build_mapped_takeoff_candidates(report)[0]

    assert candidate["quantity"] == 2
    assert candidate["unit"] == "台"
    assert candidate["confidence_class"] == "block_count"
