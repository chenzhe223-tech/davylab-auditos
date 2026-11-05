from io import BytesIO

import ezdxf
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from auditos.api import routes
from auditos.domain.models import ProjectCreate
from auditos.drawings.models import DrawingTask, EngineCapability, EngineId
from auditos.main import app
from auditos.storage.repository import ProjectRepository


def test_drawing_plan_api(tmp_path):
    routes.repository = ProjectRepository(tmp_path / "drawing-api.sqlite3")
    client = TestClient(app)
    project = routes.repository.create_project(ProjectCreate(name="安装工程图纸算量"))
    response = client.post(
        f"/api/projects/{project.id}/drawings/plan",
        json={
            "filename": "power.dwg",
            "task_description": "统计电缆桥架长度",
            "discipline": "installation",
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["decision"]["task"] == DrawingTask.LINEAR_TAKEOFF.value
    assert result["decision"]["primary_engine"] == EngineId.DWG_READER.value
    assert result["ready"] is False


def test_engine_capabilities_api(monkeypatch, tmp_path):
    routes.repository = ProjectRepository(tmp_path / "drawing-api.sqlite3")
    monkeypatch.setattr(
        "auditos.api.routes.engine_capabilities",
        lambda: [EngineCapability(
            engine_id=EngineId.DWG_READER,
            installed=False,
            available_tasks=[DrawingTask.TEXT_DIMENSIONS],
            warnings=["not installed"],
        )],
    )
    response = TestClient(app).get("/api/drawings/engines")
    assert response.status_code == 200
    assert response.json()["engines"][0]["engine_id"] == EngineId.DWG_READER.value



def test_upload_and_analyze_dxf_persists_entity_evidence(tmp_path):
    routes.repository = ProjectRepository(tmp_path / "drawing-api.sqlite3")
    client = TestClient(app)
    project = routes.repository.create_project(ProjectCreate(name="DXF 原生算量"))

    drawing_path = tmp_path / "pipe-plan.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line(
        (0, 0), (2000, 0), dxfattribs={"layer": "给水管"}
    )
    doc.saveas(drawing_path)

    upload = client.post(
        f"/api/projects/{project.id}/drawings/upload",
        files={"file": ("pipe-plan.dxf", drawing_path.read_bytes(), "application/dxf")},
        data={"revision": "A", "source_unit": "mm"},
    )
    assert upload.status_code == 200
    drawing = upload.json()
    assert drawing["filename"] == "pipe-plan.dxf"
    assert drawing["revision"] == "A"
    assert len(drawing["sha256"]) == 64

    analyzed = client.post(
        f"/api/projects/{project.id}/drawings/{drawing['id']}/analyze",
        json={
            "task_description": "统计管道长度",
            "layer_patterns": ["给水管"],
            "discipline": "plumbing",
        },
    )
    assert analyzed.status_code == 200
    run = analyzed.json()
    assert run["engine_id"] == EngineId.NATIVE.value
    assert "external_engine_runs" in run["report"]
    assert "cross_engine_comparison" in run["report"]
    assert set(run["report"]["cross_engine_comparisons"]) == {
        "dxf_civil", "cad_ai_agent", "dwg_reader"
    }
    layer = run["report"]["layers"][0]
    assert layer["layer"] == "给水管"
    assert float(layer["linear_length_m"]) == 2.0
    assert run["report"]["source_drawing_sha256"] == drawing["sha256"]
    assert run["report"]["drawing_revision"] == "A"
    assert run["report"]["mapping_suggestions"][0]["status"] == "candidate_needs_confirmation"
    assert run["report"]["mapping_suggestions"][0]["candidates"][0]["rule_id"] == "plumbing.water_supply.length"
    mapped = run["report"]["mapped_takeoff_candidates"][0]
    assert mapped["status"] == "candidate_for_human_review"
    assert float(mapped["quantity"]) == 2.0
    assert mapped["evidence_handles"]
    assert len(run["report"]["measurements"]) == 1
    assert run["report"]["measurements"][0]["entity_handle"]
    entity_handle = run["report"]["measurements"][0]["entity_handle"]
    entity_detail = client.get(
        f"/api/projects/{project.id}/drawings/{drawing['id']}/runs/{run['id']}/entities/{entity_handle}"
    )
    assert entity_detail.status_code == 200
    assert entity_detail.json()["entity"]["entity_type"] == "LINE"
    assert entity_detail.json()["entity"]["geometry"]["end"] == [2000.0, 0.0, 0.0]
    assert entity_detail.json()["requires_visual_review"] is True

    candidate_id = mapped["candidate_id"]
    candidates_url = (
        f"/api/projects/{project.id}/drawings/{drawing['id']}/runs/{run['id']}/candidates"
    )
    confirmed = client.post(
        f"{candidates_url}/{candidate_id}/review",
        json={"decision": "confirmed", "note": "图纸和尺寸已复核"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["reviewed_quantity"] == confirmed.json()["original_quantity"]

    adjusted = client.post(
        f"{candidates_url}/{candidate_id}/review",
        json={
            "decision": "adjusted",
            "reviewed_quantity": "2.1",
            "note": "现场复核后补充了连接段长度",
        },
    )
    assert adjusted.status_code == 200
    candidate_state = client.get(candidates_url).json()[0]
    assert candidate_state["latest_review"]["decision"] == "adjusted"
    assert len(candidate_state["review_history"]) == 2
    assert candidate_state["latest_review"]["reviewed_quantity"] == "2.1"

    history = client.get(
        f"/api/projects/{project.id}/drawings/{drawing['id']}/runs"
    )
    assert history.status_code == 200
    assert len(history.json()) == 1

    exported = client.get(
        f"/api/projects/{project.id}/drawings/{drawing['id']}/runs/{run['id']}/export.xlsx"
    )
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    assert workbook.sheetnames == ["算量候选", "实体证据", "图层映射", "运行信息", "复核记录"]
    assert float(workbook["算量候选"]["D2"].value) == 2.0
    assert workbook["算量候选"]["E2"].value == "adjusted"
    assert workbook["算量候选"]["F2"].value == "2.1"
    assert workbook["实体证据"]["A2"].value
    assert workbook["复核记录"]["B2"].value in {"adjusted", "confirmed"}



def test_revision_comparison_normalizes_units_and_persists_report(tmp_path):
    routes.repository = ProjectRepository(tmp_path / "revision-api.sqlite3")
    client = TestClient(app)
    project = routes.repository.create_project(ProjectCreate(name="Revision compare"))

    base_path = tmp_path / "base.dxf"
    base_doc = ezdxf.new()
    base_doc.header["$INSUNITS"] = 4
    base_doc.modelspace().add_line(
        (0, 0), (1000, 0), dxfattribs={"layer": "E-TRAY"}
    )
    base_doc.saveas(base_path)

    target_path = tmp_path / "target.dxf"
    target_doc = ezdxf.new()
    target_doc.header["$INSUNITS"] = 5
    target_doc.modelspace().add_line(
        (0, 0), (150, 0), dxfattribs={"layer": "E-TRAY"}
    )
    target_doc.saveas(target_path)

    base_upload = client.post(
        f"/api/projects/{project.id}/drawings/upload",
        files={"file": ("base.dxf", base_path.read_bytes(), "application/dxf")},
        data={"revision": "A"},
    )
    target_upload = client.post(
        f"/api/projects/{project.id}/drawings/upload",
        files={"file": ("target.dxf", target_path.read_bytes(), "application/dxf")},
        data={"revision": "B"},
    )
    assert base_upload.status_code == 200
    assert target_upload.status_code == 200

    compared = client.post(
        f"/api/projects/{project.id}/drawings/compare-revisions",
        json={
            "base_drawing_id": base_upload.json()["id"],
            "target_drawing_id": target_upload.json()["id"],
        },
    )
    assert compared.status_code == 200
    report = compared.json()["report"]
    assert report["base_source_unit"] == "mm"
    assert report["target_source_unit"] == "cm"
    assert float(report["totals"]["delta_linear_length_m"]) == 0.5
    assert report["layers"][0]["status"] == "changed"

    history = client.get(
        f"/api/projects/{project.id}/drawings/revision-comparisons"
    )
    assert history.status_code == 200
    assert len(history.json()) == 1

    comparison_id = compared.json()["id"]
    exported = client.get(
        f"/api/projects/{project.id}/drawings/revision-comparisons/{comparison_id}/export.xlsx"
    )
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    assert workbook.sheetnames == ["修订差异", "图层明细", "复核提示"]
    assert float(workbook["图层明细"]["E2"].value) == 0.5


def test_revision_comparison_blocks_unknown_units_for_linear_geometry(tmp_path):
    routes.repository = ProjectRepository(tmp_path / "revision-unknown.sqlite3")
    client = TestClient(app)
    project = routes.repository.create_project(ProjectCreate(name="Unknown units"))

    paths = []
    for filename in ("base-unknown.dxf", "target-unknown.dxf"):
        path = tmp_path / filename
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 0
        doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "P-PIPE"})
        doc.saveas(path)
        paths.append(path)

    uploads = [
        client.post(
            f"/api/projects/{project.id}/drawings/upload",
            files={"file": (path.name, path.read_bytes(), "application/dxf")},
        )
        for path in paths
    ]
    assert all(response.status_code == 200 for response in uploads)

    compared = client.post(
        f"/api/projects/{project.id}/drawings/compare-revisions",
        json={
            "base_drawing_id": uploads[0].json()["id"],
            "target_drawing_id": uploads[1].json()["id"],
        },
    )

    assert compared.status_code == 422
    assert "unit is unknown" in compared.json()["detail"]
