import ezdxf

from auditos.drawings.revisions import compare_dxf_entity_revisions


def test_same_geometry_with_new_handle_matches_across_units(tmp_path):
    base = tmp_path / "base.dxf"
    doc_a = ezdxf.new()
    doc_a.header["$INSUNITS"] = 4
    line_a = doc_a.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "E-TRAY"})
    doc_a.saveas(base)

    target = tmp_path / "target.dxf"
    doc_b = ezdxf.new()
    doc_b.header["$INSUNITS"] = 5
    model = doc_b.modelspace()
    model.add_text("note", dxfattribs={"layer": "ANNO"})
    line_b = model.add_line((0, 0), (100, 0), dxfattribs={"layer": "E-TRAY"})
    doc_b.saveas(target)

    assert line_a.dxf.handle != line_b.dxf.handle
    result = compare_dxf_entity_revisions(base, target, "mm", "cm")
    assert result["summary"]["unchanged_geometry_handle_changed"] == 1
    assert result["summary"]["added"] == 0
    assert result["summary"]["removed"] == 0


def test_changed_geometry_with_same_handle_is_reported(tmp_path):
    base = tmp_path / "base-modified.dxf"
    target = tmp_path / "target-modified.dxf"
    doc_a = ezdxf.new()
    doc_a.header["$INSUNITS"] = 4
    doc_a.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "P-PIPE"})
    doc_a.saveas(base)
    doc_b = ezdxf.new()
    doc_b.header["$INSUNITS"] = 5
    doc_b.modelspace().add_line((0, 0), (120, 0), dxfattribs={"layer": "P-PIPE"})
    doc_b.saveas(target)

    result = compare_dxf_entity_revisions(base, target, "mm", "cm")
    assert result["summary"]["modified_same_handle"] == 1
    assert result["total_changed_entities"] == 1
