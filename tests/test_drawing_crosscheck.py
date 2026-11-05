from auditos.drawings.crosscheck import compare_dxf_civil_takeoff


def test_cross_engine_comparison_matches_only_semantically_compatible_metrics():
    native = {
        "layers": [{"layer": "P-PIPE", "block_counts": {"VALVE": 2}}],
        "measurements": [
            {"layer": "P-PIPE", "entity_type": "LINE", "measurement_kind": "length", "raw_value": "5"},
            {"layer": "P-PIPE", "entity_type": "ARC", "measurement_kind": "length", "raw_value": "2"},
            {"layer": "P-PIPE", "entity_type": "LWPOLYLINE", "measurement_kind": "length", "raw_value": "3", "has_bulge": False},
            {"layer": "P-PIPE", "entity_type": "LWPOLYLINE", "measurement_kind": "area", "raw_value": "4", "has_bulge": False},
            {"layer": "P-PIPE", "entity_type": "CIRCLE", "measurement_kind": "area", "raw_value": "1"},
        ],
    }
    external = {
        "layers": [{
            "layer": "P-PIPE", "total_length": 10,
            "closed_polyline_area": 4, "circle_total_area": 1,
            "block_inserts": {"VALVE": 2},
        }]
    }

    result = compare_dxf_civil_takeoff(native, external)

    assert result["overall_status"] == "match"
    assert {item["status"] for item in result["layers"][0]["metrics"]} == {"match"}


def test_cross_engine_comparison_withholds_bulge_sensitive_metrics():
    native = {
        "layers": [{"layer": "P-PIPE", "block_counts": {}}],
        "measurements": [
            {"layer": "P-PIPE", "entity_type": "LWPOLYLINE", "measurement_kind": "length", "raw_value": "10", "has_bulge": True},
            {"layer": "P-PIPE", "entity_type": "LWPOLYLINE", "measurement_kind": "area", "raw_value": "5", "has_bulge": True},
        ],
    }
    external = {
        "layers": [{
            "layer": "P-PIPE", "total_length": 9,
            "closed_polyline_area": 4, "circle_total_area": 0,
            "block_inserts": {},
        }]
    }

    result = compare_dxf_civil_takeoff(native, external)
    metrics = {item["metric"]: item for item in result["layers"][0]["metrics"]}

    assert metrics["linear_length"]["status"] == "not_comparable_upstream_geometry_limit"
    assert metrics["closed_polyline_area"]["status"] == "not_comparable_upstream_geometry_limit"
    assert result["overall_status"] == "partial_or_not_comparable"


def test_cross_engine_comparison_reports_real_differences():
    native = {
        "layers": [{"layer": "E-TRAY", "block_counts": {}}],
        "measurements": [{
            "layer": "E-TRAY", "entity_type": "LINE",
            "measurement_kind": "length", "raw_value": "100",
        }],
    }
    external = {
        "layers": [{
            "layer": "E-TRAY", "total_length": 95,
            "closed_polyline_area": 0, "circle_total_area": 0,
            "block_inserts": {},
        }]
    }

    result = compare_dxf_civil_takeoff(native, external)

    assert result["overall_status"] == "differences_found"
    assert result["layers"][0]["metrics"][0]["status"] == "difference"
    assert result["layers"][0]["metrics"][0]["difference"] == "5"
