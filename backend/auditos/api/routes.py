import hashlib
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from auditos.audit_engine.settlement_checks import review_rows
from auditos.domain.models import ProjectCreate, ProjectRead
from auditos.drawings.adapters import DwgReaderAdapter
from auditos.drawings.geometry import analyze_dxf_geometry
from auditos.drawings.models import DrawingAnalysisRequest, TakeoffRequest
from auditos.drawings.router import classify_task
from auditos.drawings.service import engine_capabilities, plan_takeoff
from auditos.ingestion.settlement_xlsx import read_settlement_xlsx
from auditos.storage.repository import ProjectRepository

router = APIRouter()
repository = ProjectRepository()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DRAWING_UPLOAD_BYTES = 250 * 1024 * 1024
UPLOAD_FIELD = File(...)


class FindingStatusUpdate(BaseModel):
    status: Literal["needs_evidence", "pending_review", "accepted", "rejected", "adjusted", "closed"]
    note: str | None = Field(default=None, max_length=2000)


@router.post("/projects", response_model=ProjectRead, tags=["projects"])
def create_project(payload: ProjectCreate) -> ProjectRead:
    return repository.create_project(payload)


@router.get("/projects", response_model=list[ProjectRead], tags=["projects"])
def list_projects() -> list[ProjectRead]:
    return repository.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
def get_project(project_id: str) -> ProjectRead:
    project = repository.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/drawings/engines", tags=["drawings"])
def get_drawing_engines() -> dict:
    return {"engines": [item.model_dump(mode="json") for item in engine_capabilities()]}


@router.post("/projects/{project_id}/drawings/plan", tags=["drawings"])
def plan_drawing_takeoff(project_id: str, payload: TakeoffRequest) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return plan_takeoff(payload)


@router.post("/projects/{project_id}/drawings/upload", tags=["drawings"])
async def upload_drawing(
    project_id: str,
    file: UploadFile = UPLOAD_FIELD,
    revision: str | None = Form(default=None, max_length=80),
    source_unit: str | None = Form(default=None, max_length=40),
) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    filename = Path(file.filename or "drawing.dxf").name
    extension = Path(filename).suffix.lower()
    if extension not in {".dwg", ".dxf"}:
        raise HTTPException(status_code=415, detail="Only .dwg and .dxf drawings are supported")
    data_root = repository.path.parent / "drawings" / project_id
    data_root.mkdir(parents=True, exist_ok=True)
    temporary_path = data_root / f".upload-{uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DRAWING_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Drawing exceeds 250 MiB limit")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded drawing is empty")
        file_hash = digest.hexdigest()
        drawing_dir = data_root / file_hash
        drawing_dir.mkdir(parents=True, exist_ok=True)
        stored_path = drawing_dir / filename
        temporary_path.replace(stored_path)
        return repository.create_drawing(
            project_id, filename, file_hash, size, str(stored_path), revision, source_unit
        )
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Unable to store drawing locally") from exc
    finally:
        await file.close()


@router.get("/projects/{project_id}/drawings", tags=["drawings"])
def list_project_drawings(project_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return repository.list_drawings(project_id)


@router.post("/projects/{project_id}/drawings/{drawing_id}/analyze", tags=["drawings"])
def analyze_uploaded_drawing(
    project_id: str, drawing_id: str, payload: DrawingAnalysisRequest
) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    drawing = repository.get_drawing(drawing_id, project_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    task = classify_task(payload.task_description, payload.measurement_kind)
    if task not in {"linear_takeoff", "area_takeoff", "block_count"}:
        raise HTTPException(
            status_code=422,
            detail="Phase 1 execution supports linear takeoff, closed-area measurement and block counts.",
        )

    source_path = Path(drawing["stored_path"])
    run_dir = source_path.parent / "runs" / str(uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)
    measurement_path = source_path
    conversion_result = None
    if source_path.suffix.lower() == ".dwg":
        try:
            conversion_result = DwgReaderAdapter().execute(task, source_path, run_dir)
            converted_path = conversion_result.get("converted_dxf")
            if not converted_path:
                raise RuntimeError("DWG conversion did not return a DXF path.")
            measurement_path = Path(converted_path)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"DWG conversion unavailable: {type(exc).__name__}",
            ) from exc

    try:
        report = analyze_dxf_geometry(
            measurement_path,
            layer_patterns=payload.layer_patterns,
            source_unit_override=payload.source_unit_override or drawing.get("source_unit"),
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Drawing analysis failed: {exc}") from exc

    report["project_id"] = project_id
    report["drawing_id"] = drawing_id
    report["source_filename"] = drawing["filename"]
    report["source_drawing_sha256"] = drawing["sha256"]
    report["drawing_revision"] = drawing.get("revision")
    report["task"] = task.value if hasattr(task, "value") else str(task)
    if conversion_result:
        report["conversion"] = {
            "engine": conversion_result["engine"],
            "engine_version": conversion_result.get("engine_version"),
            "converted_dxf": str(measurement_path),
            "candidate_output": conversion_result.get("output_file"),
        }
    saved_run = repository.create_drawing_run(
        project_id, drawing_id, str(task), "auditos_native", report
    )
    return saved_run


@router.get("/projects/{project_id}/drawings/{drawing_id}/runs", tags=["drawings"])
def list_drawing_analysis_runs(project_id: str, drawing_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if repository.get_drawing(drawing_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    return repository.list_drawing_runs(drawing_id, project_id)


@router.post("/projects/{project_id}/settlements/audit", tags=["settlement"])
async def audit_settlement(project_id: str, file: UploadFile = UPLOAD_FIELD) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(status_code=415, detail="Only .xlsx files are supported in this release")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MiB limit")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    digest = hashlib.sha256(content).hexdigest()
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as temporary:
            temporary.write(content)
            temporary.flush()
            rows = read_settlement_xlsx(temporary.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to parse workbook: {type(exc).__name__}") from exc

    import_id = repository.create_import(
        project_id, Path(file.filename or "settlement.xlsx").name, digest, len(rows)
    )
    findings = review_rows(rows)
    serialized = [
        {
            "code": item.code, "category": item.category, "title": item.title,
            "description": item.description, "source_ref": item.source_ref,
            "rule_id": item.rule_id, "rule_version": item.rule_version,
            "declared_amount": str(item.declared_amount) if item.declared_amount is not None else None,
            "calculated_amount": str(item.calculated_amount) if item.calculated_amount is not None else None,
            "difference_amount": str(item.difference_amount) if item.difference_amount is not None else None,
            "status": item.status, "requires_human_review": item.requires_human_review,
        }
        for item in findings
    ]
    saved = repository.save_findings(project_id, import_id, serialized)
    return {
        "project_id": project_id, "import_id": import_id,
        "filename": Path(file.filename or "settlement.xlsx").name,
        "sha256": digest, "rows_read": len(rows), "findings_count": len(saved),
        "notice": "规则触发项是审核线索，不等于确认审减金额；请逐条人工复核。",
        "findings": saved,
    }


@router.get("/projects/{project_id}/findings", tags=["settlement"])
def list_project_findings(project_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return repository.list_findings(project_id)


@router.patch("/findings/{finding_id}/status", tags=["settlement"])
def update_finding_status(finding_id: str, payload: FindingStatusUpdate) -> dict:
    result = repository.update_finding_status(finding_id, payload.status, payload.note)
    if result is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return result


@router.get("/projects/{project_id}/findings/export.xlsx", tags=["reports"])
def export_findings(project_id: str) -> StreamingResponse:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    findings = repository.list_findings(project_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "审核问题台账"
    headers = [
        "发现编号", "问题代码", "类别", "审核事项", "说明", "来源定位",
        "规则编号", "规则版本", "申报金额", "计算金额", "差异金额", "处理状态", "需人工复核",
    ]
    sheet.append(headers)
    for item in findings:
        sheet.append([
            item["id"], item["code"], item["category"], item["title"], item["description"],
            item["source_ref"], item["rule_id"], item["rule_version"], item["declared_amount"],
            item["calculated_amount"], item["difference_amount"], item["status"],
            "是" if item["requires_human_review"] else "否",
        ])
    for column in sheet.columns:
        letter = column[0].column_letter
        longest = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[letter].width = min(max(longest + 2, 12), 48)
    with tempfile.SpooledTemporaryFile() as output:
        workbook.save(output)
        output.seek(0)
        content = output.read()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="auditos-findings-{project_id}.xlsx"'},
    )
