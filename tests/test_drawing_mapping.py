from auditos.drawings.mapping import get_mapping_templates, suggest_layer_mappings


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
