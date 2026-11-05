from fastapi.testclient import TestClient

from auditos.api import routes
from auditos.domain.models import ProjectCreate
from auditos.drawings.models import EngineCapability, EngineId, DrawingTask
from auditos.main import app
from auditos.storage.repository import ProjectRepository


def test_drawing_plan_api(tmp_path, monkeypatch):
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


def test_engine_capabilities_api(monkeypatch):
    routes.repository = ProjectRepository()
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
