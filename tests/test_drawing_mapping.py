from auditos.domain.models import ProjectCreate
from auditos.drawings.mapping import get_mapping_templates, suggest_layer_mappings
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
    result = suggest_layer_mappings(["给水管-立管"], "plumbing", saved)[0]

    assert result["mapping_source"] == "project_custom"
    assert result["candidates"][0]["item_name"] == "项目自定义给水管"
    assert result["candidates"][0]["version"] == 2
