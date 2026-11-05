from fastapi.testclient import TestClient
import ezdxf

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
        (0, 0), (2000, 0), dxfattribs={"layer": "M-PIPE"}
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
            "layer_patterns": ["M-PIPE"],
        },
    )
    assert analyzed.status_code == 200
    run = analyzed.json()
    assert run["engine_id"] == EngineId.NATIVE.value
    layer = run["report"]["layers"][0]
    assert layer["layer"] == "M-PIPE"
    assert float(layer["linear_length_m"]) == 2.0
    assert run["report"]["source_drawing_sha256"] == drawing["sha256"]
    assert run["report"]["drawing_revision"] == "A"
    assert len(run["report"]["measurements"]) == 1
    assert run["report"]["measurements"][0]["entity_handle"]

    history = client.get(
        f"/api/projects/{project.id}/drawings/{drawing['id']}/runs"
    )
    assert history.status_code == 200
    assert len(history.json()) == 1
