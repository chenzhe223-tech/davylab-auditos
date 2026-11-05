from io import BytesIO
from decimal import Decimal

from fastapi.testclient import TestClient
from openpyxl import Workbook

from auditos.main import app
from auditos.storage.repository import ProjectRepository


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def make_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "结算明细"
    sheet.append(["编码", "项目名称", "单位", "数量", "综合单价", "合价"])
    sheet.append(["0301", "电缆敷设", "m", 10, 5, 60])
    sheet.append(["0301", "电缆敷设", "m", 10, 5, 50])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_project_create_audit_and_export(tmp_path, monkeypatch):
    from auditos.api import routes
    routes.repository = ProjectRepository(tmp_path / "api.sqlite3")
    client = TestClient(app)

    project = client.post("/api/projects", json={"name": "安装工程结算审核"})
    assert project.status_code == 200
    project_id = project.json()["id"]

    audit = client.post(
        f"/api/projects/{project_id}/settlements/audit",
        files={"file": ("settlement.xlsx", make_workbook(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert audit.status_code == 200
    result = audit.json()
    assert result["rows_read"] == 2
    assert result["findings_count"] == 2

    finding_id = result["findings"][0]["id"]
    status = client.patch(
        f"/api/findings/{finding_id}/status",
        json={"status": "accepted", "note": "复核确认算术差异"},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "accepted"

    exported = client.get(f"/api/projects/{project_id}/findings/export.xlsx")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )


def test_upload_rejects_non_xlsx():
    client = TestClient(app)
    response = client.post(
        "/api/projects/not-real/settlements/audit",
        files={"file": ("source.csv", b"x,y", "text/csv")},
    )
    assert response.status_code == 404
