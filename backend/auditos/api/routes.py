import hashlib
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from auditos.audit_engine.settlement_checks import review_rows
from auditos.domain.models import ProjectCreate, ProjectRead
from auditos.ingestion.settlement_xlsx import read_settlement_xlsx
from auditos.storage.repository import ProjectRepository

router = APIRouter()
repository = ProjectRepository()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


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


@router.post("/projects/{project_id}/settlements/audit", tags=["settlement"])
async def audit_settlement(project_id: str, file: UploadFile = File(...)) -> dict:
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
        "project_id": project_id, "import_id": import_id, "filename": Path(file.filename or "settlement.xlsx").name,
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
    headers = ["发现编号", "问题代码", "类别", "审核事项", "说明", "来源定位", "规则编号",
               "规则版本", "申报金额", "计算金额", "差异金额", "处理状态", "需人工复核"]
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
        sheet.column_dimensions[letter].width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 48)
    output = tempfile.SpooledTemporaryFile()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="auditos-findings-{project_id}.xlsx"'},
    )
