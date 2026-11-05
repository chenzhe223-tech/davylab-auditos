import math

import ezdxf

from auditos.drawings.geometry import analyze_dxf_geometry


def _save_drawing(path, units=4):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    model = doc.modelspace()
    model.add_line((0, 0), (3000, 4000), dxfattribs={"layer": "E-CABLE"})
    model.add_arc((0, 0), radius=1000, start_angle=0, end_angle=90,
                  dxfattribs={"layer": "E-ARC"})
    model.add_circle((0, 0), radius=1000, dxfattribs={"layer": "E-DEVICE"})
    model.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        close=True,
        dxfattribs={"layer": "E-AREA"},
    )
    block = doc.blocks.new("LIGHT_FIXTURE")
    block.add_circle((0, 0), radius=50)
    model.add_blockref("LIGHT_FIXTURE", (500, 500), dxfattribs={"layer": "E-DEVICE"})
    doc.saveas(path)


def test_deterministic_measurements_and_unit_conversion(tmp_path):
    path = tmp_path / "installation.dxf"
    _save_drawing(path)

    report = analyze_dxf_geometry(path)
    layers = {item["layer"]: item for item in report["layers"]}

    assert report["unit_status"] == "confirmed"
    assert report["source_unit"] == "mm"
    assert math.isclose(float(layers["E-CABLE"]["linear_length_m"]), 5.0)
    assert math.isclose(float(layers["E-ARC"]["linear_length_m"]), math.pi / 2, rel_tol=1e-9)
    assert math.isclose(float(layers["E-AREA"]["closed_area_m2"]), 1.0)
    area_evidence = next(
        row for row in report["measurements"]
        if row["layer"] == "E-AREA" and row["measurement_kind"] == "area"
    )
    assert area_evidence["source_unit"] == "mm2"
    assert layers["E-DEVICE"]["block_counts"] == {"LIGHT_FIXTURE": 1}
    assert any(row["entity_type"] == "CIRCLE" and row["measurement_kind"] == "area"
               for row in report["measurements"])
    assert len(report["drawing_sha256"]) == 64


def test_unknown_units_withhold_normalized_quantities(tmp_path):
    path = tmp_path / "unitless.dxf"
    _save_drawing(path, units=0)

    report = analyze_dxf_geometry(path)

    assert report["unit_status"] == "requires_confirmation"
    assert report["normalized_length_unit"] is None
    assert report["measurements"][0]["normalized_value"] is None
    assert any(item.startswith("UNIT_UNKNOWN") for item in report["warnings"])


def test_layer_filter_is_case_insensitive(tmp_path):
    path = tmp_path / "filtered.dxf"
    _save_drawing(path)

    report = analyze_dxf_geometry(path, layer_patterns=["e-cab*"])

    assert [item["layer"] for item in report["layers"]] == ["E-CABLE"]
    assert all(row["layer"] == "E-CABLE" for row in report["measurements"])


def test_bulge_polyline_uses_arc_length_and_segment_area(tmp_path):
    path = tmp_path / "bulge.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    doc.modelspace().add_lwpolyline(
        [(0, 0, 1), (2, 0, 0)],
        format="xyb",
        close=True,
        dxfattribs={"layer": "BOUNDARY"},
    )
    doc.saveas(path)

    report = analyze_dxf_geometry(path)
    layer = report["layers"][0]

    assert math.isclose(float(layer["linear_length_m"]), math.pi + 2, rel_tol=1e-9)
    assert math.isclose(float(layer["closed_area_m2"]), math.pi / 2, rel_tol=1e-9)
