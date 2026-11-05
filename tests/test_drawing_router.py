import pytest

from auditos.drawings.models import DrawingTask, EngineId, TakeoffRequest
from auditos.drawings.router import classify_task, route_drawing_task
from auditos.drawings.service import inspect_drawing_file


def test_task_intent_classification():
    assert classify_task("统计电缆桥架长度") == DrawingTask.LINEAR_TAKEOFF
    assert classify_task("计算房间面积") == DrawingTask.AREA_TAKEOFF
    assert classify_task("统计设备台数") == DrawingTask.BLOCK_COUNT


def test_dwg_routes_through_dwg_reader():
    decision = route_drawing_task(TakeoffRequest(
        filename="installation.dwg", task_description="统计管道长度"
    ))
    assert decision.primary_engine == EngineId.DWG_READER
    assert EngineId.DXF_CIVIL in decision.secondary_engines
    assert decision.requires_human_review


def test_dxf_area_routes_to_visual_civil_engine():
    decision = route_drawing_task(TakeoffRequest(
        filename="floor-plan.dxf", task_description="计算闭合房间面积"
    ))
    assert decision.primary_engine == EngineId.NATIVE
    assert EngineId.DXF_CIVIL in decision.secondary_engines
    assert decision.requires_visual_review
    assert any("近似包围盒" in item for item in decision.safety_constraints)


def test_drawing_file_guards(tmp_path):
    bad = tmp_path / "plan.pdf"
    bad.write_bytes(b"pdf")
    with pytest.raises(ValueError):
        inspect_drawing_file(bad)
    empty = tmp_path / "empty.dxf"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        inspect_drawing_file(empty)



def test_dxf_linear_takeoff_uses_native_geometry_first():
    decision = route_drawing_task(TakeoffRequest(
        filename="cable-tray.dxf", task_description="统计电缆桥架长度"
    ))
    assert decision.primary_engine == EngineId.NATIVE
    assert EngineId.DWG_READER in decision.secondary_engines
    assert decision.requires_human_review
