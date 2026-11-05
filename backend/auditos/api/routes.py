import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from auditos.audit_engine.settlement_checks import review_rows
from auditos.domain.models import ProjectCreate, ProjectRead
from auditos.drawings.adapters import CadAiAgentAdapter, DwgReaderAdapter, DxfCivilAdapter
from auditos.drawings.crosscheck import (
    compare_cad_ai_takeoff,
    compare_dwg_reader_csv,
    compare_dxf_civil_takeoff,
)
from auditos.drawings.geometry import analyze_dxf_geometry, extract_entity_geometry
from auditos.drawings.mapping import (
    build_mapped_takeoff_candidates,
    get_mapping_templates,
    suggest_layer_mappings,
)
from auditos.drawings.models import (
    CandidateReviewRequest,
    DrawingAnalysisRequest,
    DrawingRevisionCompareRequest,
    ProjectLayerMapping,
    TakeoffRequest,
)
from auditos.drawings.revisions import compare_drawing_revision_reports
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


@router.post("/projects/{project_id}/drawings/compare-revisions", tags=["drawings"])
def compare_project_drawing_revisions(
    project_id: str, payload: DrawingRevisionCompareRequest
) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.base_drawing_id == payload.target_drawing_id:
        raise HTTPException(status_code=422, detail="Base and target drawings must be different")
    base = repository.get_drawing(payload.base_drawing_id, project_id)
    target = repository.get_drawing(payload.target_drawing_id, project_id)
    if base is None or target is None:
        raise HTTPException(status_code=404, detail="Both drawings must belong to this project")
    if Path(base["stored_path"]).suffix.lower() != ".dxf" or Path(target["stored_path"]).suffix.lower() != ".dxf":
        raise HTTPException(
            status_code=415,
            detail="Revision comparison currently accepts DXF files only; convert DWG to DXF first.",
        )
    try:
        base_report = analyze_dxf_geometry(
            Path(base["stored_path"]),
            source_unit_override=payload.base_source_unit_override or base.get("source_unit"),
        )
        target_report = analyze_dxf_geometry(
            Path(target["stored_path"]),
            source_unit_override=payload.target_source_unit_override or target.get("source_unit"),
        )
        if base_report["drawing_sha256"] != base["sha256"]:
            raise ValueError("Base drawing hash changed after upload.")
        if target_report["drawing_sha256"] != target["sha256"]:
            raise ValueError("Target drawing hash changed after upload.")
        comparison = compare_drawing_revision_reports(base_report, target_report)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Revision comparison failed: {exc}") from exc

    comparison["base_drawing"] = {
        "id": base["id"], "filename": base["filename"],
        "revision": base.get("revision"), "sha256": base["sha256"],
    }
    comparison["target_drawing"] = {
        "id": target["id"], "filename": target["filename"],
        "revision": target.get("revision"), "sha256": target["sha256"],
    }
    return repository.create_drawing_revision_comparison(
        project_id, payload.base_drawing_id, payload.target_drawing_id, comparison
    )


@router.get("/projects/{project_id}/drawings/revision-comparisons", tags=["drawings"])
def list_project_drawing_revision_comparisons(project_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return repository.list_drawing_revision_comparisons(project_id)


@router.get(
    "/projects/{project_id}/drawings/revision-comparisons/{comparison_id}/export.xlsx",
    tags=["reports"],
)
def export_drawing_revision_comparison(project_id: str, comparison_id: str) -> StreamingResponse:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    comparison = repository.get_drawing_revision_comparison(comparison_id, project_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Revision comparison not found")

    report = comparison["report"]
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "修订差异"
    summary_sheet.append(["字段", "值"])
    for key, value in [
        ("对比ID", comparison_id),
        ("基准修订", report.get("base_revision")),
        ("目标修订", report.get("target_revision")),
        ("基准图纸SHA-256", report.get("base_drawing_sha256")),
        ("目标图纸SHA-256", report.get("target_drawing_sha256")),
        ("基准单位", report.get("base_source_unit")),
        ("目标单位", report.get("target_source_unit")),
        ("基准总长度(m)", report.get("totals", {}).get("base_linear_length_m")),
        ("目标总长度(m)", report.get("totals", {}).get("target_linear_length_m")),
        ("长度变化(m)", report.get("totals", {}).get("delta_linear_length_m")),
        ("基准闭合面积(m²)", report.get("totals", {}).get("base_closed_area_m2")),
        ("目标闭合面积(m²)", report.get("totals", {}).get("target_closed_area_m2")),
        ("面积变化(m²)", report.get("totals", {}).get("delta_closed_area_m2")),
        ("新增图层", report.get("summary", {}).get("added_layers")),
        ("删除图层", report.get("summary", {}).get("removed_layers")),
        ("变化图层", report.get("summary", {}).get("changed_layers")),
        ("未变化图层", report.get("summary", {}).get("unchanged_layers")),
    ]:
        summary_sheet.append([key, str(value) if value is not None else None])

    layer_sheet = workbook.create_sheet("图层明细")
    layer_sheet.append([
        "图层", "状态", "基准长度(m)", "目标长度(m)", "长度变化(m)",
        "基准面积(m²)", "目标面积(m²)", "面积变化(m²)",
        "基准实体数", "目标实体数", "实体数变化", "块数量变化",
    ])
    for item in report.get("layers", []):
        layer_sheet.append([
            item.get("layer"), item.get("status"),
            str(item.get("base_linear_length_m")),
            str(item.get("target_linear_length_m")),
            str(item.get("delta_linear_length_m")),
            str(item.get("base_closed_area_m2")),
            str(item.get("target_closed_area_m2")),
            str(item.get("delta_closed_area_m2")),
            item.get("base_entity_count"), item.get("target_entity_count"),
            item.get("delta_entity_count"),
            "; ".join(f"{name}: {delta}" for name, delta in item.get("delta_block_counts", {}).items()),
        ])

    notes_sheet = workbook.create_sheet("复核提示")
    notes_sheet.append(["说明"])
    for note in report.get("limitations", []):
        notes_sheet.append([note])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            longest = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[letter].width = min(max(longest + 2, 12), 56)

    with tempfile.SpooledTemporaryFile() as output:
        workbook.save(output)
        output.seek(0)
        content = output.read()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="auditos-revision-{comparison_id}.xlsx"'
        },
    )


@router.get("/drawings/mapping-templates", tags=["drawings"])
def list_drawing_mapping_templates(discipline: str = "all") -> dict:
    return {
        "discipline": discipline,
        "templates": get_mapping_templates(discipline),
        "notice": "模板仅用于图层识别建议，不代表国家规范、地方定额或项目已确认计量规则。",
    }


@router.get("/projects/{project_id}/drawings/mappings", tags=["drawings"])
def list_project_layer_mappings(project_id: str) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "mappings": repository.list_project_layer_mappings(project_id)}


@router.get(
    "/projects/{project_id}/drawings/mappings/{rule_id}/history", tags=["drawings"]
)
def list_project_layer_mapping_history(project_id: str, rule_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return repository.list_project_layer_mapping_history(project_id, rule_id)


@router.post("/projects/{project_id}/drawings/mappings", tags=["drawings"])
def save_project_layer_mapping(project_id: str, payload: ProjectLayerMapping) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return repository.save_project_layer_mapping(
        project_id, payload.model_dump(mode="json")
    )


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

    external_engine_runs = []
    unavailable_status = "disabled" if not payload.include_external_crosschecks else "not_available"
    cross_engine_comparisons = {
        "dxf_civil": {"status": unavailable_status},
        "cad_ai_agent": {"status": unavailable_status},
        "dwg_reader": {"status": unavailable_status},
    }
    if conversion_result:
        external_engine_runs.append({
            "engine": "dwg_reader",
            "status": "completed",
            "candidate_only": True,
            "result": conversion_result,
        })

    if payload.include_external_crosschecks:
        external_adapters = [
            DwgReaderAdapter(),
            CadAiAgentAdapter(),
            DxfCivilAdapter(),
        ]
        for adapter in external_adapters:
            if adapter.engine_id.value == "dwg_reader" and conversion_result:
                continue
            capability = adapter.probe()
            if not capability.installed:
                external_engine_runs.append({
                    "engine": adapter.engine_id.value,
                    "status": "not_installed",
                    "candidate_only": True,
                    "warnings": capability.warnings,
                })
                continue
            if task not in capability.available_tasks:
                external_engine_runs.append({
                    "engine": adapter.engine_id.value,
                    "status": "unsupported_task",
                    "candidate_only": True,
                })
                continue
            try:
                engine_result = adapter.execute(
                    task, measurement_path, run_dir / adapter.engine_id.value
                )
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                external_engine_runs.append({
                    "engine": adapter.engine_id.value,
                    "status": "failed",
                    "candidate_only": True,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                })
                continue
            external_engine_runs.append({
                "engine": adapter.engine_id.value,
                "status": "completed",
                "candidate_only": True,
                "result": engine_result,
            })
            try:
                if adapter.engine_id.value == "dxf_civil" and engine_result.get("report"):
                    cross_engine_comparisons["dxf_civil"] = compare_dxf_civil_takeoff(
                        report, engine_result["report"]
                    )
                elif adapter.engine_id.value == "cad_ai_agent" and engine_result.get("report"):
                    cross_engine_comparisons["cad_ai_agent"] = compare_cad_ai_takeoff(
                        report, engine_result["report"]
                    )
                elif adapter.engine_id.value == "dwg_reader" and engine_result.get("output_file"):
                    cross_engine_comparisons["dwg_reader"] = compare_dwg_reader_csv(
                        report, engine_result["output_file"]
                    )
            except (OSError, KeyError, TypeError, ValueError) as exc:
                cross_engine_comparisons[adapter.engine_id.value] = {
                    "status": "normalization_failed",
                    "error_type": type(exc).__name__,
                    "note": "保留上游原始输出，不进行数值对比。",
                }

    if conversion_result and payload.include_external_crosschecks:
        try:
            cross_engine_comparisons["dwg_reader"] = compare_dwg_reader_csv(
                report, conversion_result.get("output_file", "")
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            cross_engine_comparisons["dwg_reader"] = {
                "status": "normalization_failed",
                "error_type": type(exc).__name__,
            }

    report["project_id"] = project_id
    report["drawing_id"] = drawing_id
    report["source_filename"] = drawing["filename"]
    report["source_drawing_sha256"] = drawing["sha256"]
    report["drawing_revision"] = drawing.get("revision")
    report["task"] = task.value if hasattr(task, "value") else str(task)
    report["external_engine_runs"] = external_engine_runs
    report["cross_engine_comparisons"] = cross_engine_comparisons
    report["cross_engine_comparison"] = cross_engine_comparisons["dxf_civil"]
    report["mapping_suggestions"] = suggest_layer_mappings(
        [item["layer"] for item in report["layers"]],
        payload.discipline,
        repository.list_project_layer_mappings(project_id),
        {item["layer"]: list(item["entity_types"]) for item in report["layers"]},
    )
    report["mapped_takeoff_candidates"] = build_mapped_takeoff_candidates(report)
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


@router.get(
    "/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/candidates",
    tags=["drawings"],
)
def list_takeoff_candidates(project_id: str, drawing_id: str, run_id: str) -> list[dict]:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if repository.get_drawing(drawing_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    run = repository.get_drawing_run(run_id, drawing_id, project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Drawing analysis run not found")
    reviews = repository.list_drawing_candidate_reviews(run_id)
    by_candidate: dict[str, list[dict]] = {}
    for review in reviews:
        by_candidate.setdefault(review["candidate_id"], []).append(review)
    result = []
    for candidate in run["report"].get("mapped_takeoff_candidates", []):
        history = by_candidate.get(candidate["candidate_id"], [])
        result.append({
            **candidate,
            "latest_review": history[0] if history else None,
            "review_history": history,
        })
    return result


@router.post(
    "/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/candidates/{candidate_id}/review",
    tags=["drawings"],
)
def review_takeoff_candidate(
    project_id: str,
    drawing_id: str,
    run_id: str,
    candidate_id: str,
    payload: CandidateReviewRequest,
) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if repository.get_drawing(drawing_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    run = repository.get_drawing_run(run_id, drawing_id, project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Drawing analysis run not found")
    candidate = next(
        (item for item in run["report"].get("mapped_takeoff_candidates", [])
         if item.get("candidate_id") == candidate_id),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Takeoff candidate not found")

    original_quantity = candidate.get("quantity")
    reviewed_quantity = None
    if payload.decision in {"confirmed", "adjusted"}:
        if candidate.get("status") != "candidate_for_human_review" or original_quantity is None:
            raise HTTPException(
                status_code=409,
                detail="Only numeric candidates with confirmed mapping and units can be confirmed or adjusted.",
            )
        if payload.decision == "confirmed":
            reviewed_quantity = str(original_quantity)
        else:
            if payload.reviewed_quantity is None or len(payload.note.strip()) < 3:
                raise HTTPException(
                    status_code=422,
                    detail="Adjusted quantity requires a non-negative value and an explanation.",
                )
            reviewed_quantity = str(payload.reviewed_quantity)
    elif payload.reviewed_quantity is not None:
        raise HTTPException(
            status_code=422,
            detail="Rejected candidates cannot carry a reviewed quantity.",
        )

    return repository.create_drawing_candidate_review(
        project_id=project_id,
        drawing_id=drawing_id,
        run_id=run_id,
        candidate_id=candidate_id,
        decision=payload.decision,
        original_quantity=str(original_quantity) if original_quantity is not None else None,
        reviewed_quantity=reviewed_quantity,
        note=payload.note.strip(),
        reviewer=payload.reviewer,
    )


@router.get(
    "/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/entities/{entity_handle}",
    tags=["drawings"],
)
def get_drawing_entity_evidence(
    project_id: str, drawing_id: str, run_id: str, entity_handle: str
) -> dict:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    drawing = repository.get_drawing(drawing_id, project_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    run = repository.get_drawing_run(run_id, drawing_id, project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Drawing analysis run not found")

    report = run["report"]
    evidence_rows = [
        item for item in report.get("measurements", [])
        if str(item.get("entity_handle", "")).upper() == entity_handle.upper()
    ]
    if not evidence_rows:
        raise HTTPException(status_code=404, detail="No measurement evidence for this entity Handle")

    conversion = report.get("conversion") or {}
    evidence_path = Path(conversion.get("converted_dxf") or drawing["stored_path"])
    try:
        entity = extract_entity_geometry(evidence_path, entity_handle)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Source geometry is unavailable or changed") from exc
    if entity["drawing_sha256"] != report.get("drawing_sha256"):
        raise HTTPException(status_code=409, detail="Drawing content hash differs from analyzed revision")

    return {
        "project_id": project_id,
        "drawing_id": drawing_id,
        "run_id": run_id,
        "source_drawing_sha256": report.get("source_drawing_sha256"),
        "measurement_evidence": evidence_rows,
        "entity": entity,
        "requires_visual_review": True,
    }


@router.get(
    "/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/export.xlsx",
    tags=["reports"],
)
def export_drawing_takeoff(
    project_id: str, drawing_id: str, run_id: str
) -> StreamingResponse:
    if repository.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if repository.get_drawing(drawing_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Drawing not found in this project")
    run = repository.get_drawing_run(run_id, drawing_id, project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Drawing analysis run not found")

    report = run["report"]
    workbook = Workbook()
    candidates_sheet = workbook.active
    candidates_sheet.title = "算量候选"
    review_records = repository.list_drawing_candidate_reviews(run_id)
    latest_reviews = {}
    for review in review_records:
        latest_reviews.setdefault(review["candidate_id"], review)
    candidates_sheet.append([
        "专业", "候选清单项", "单位", "候选数量", "复核决定", "复核后数量",
        "来源图层", "状态", "可信等级", "实体Handle", "需人工复核", "说明",
    ])
    for item in report.get("mapped_takeoff_candidates", []):
        latest_review = latest_reviews.get(item.get("candidate_id"), {})
        candidates_sheet.append([
            item.get("discipline"), item.get("item_name"), item.get("unit"),
            str(item.get("quantity")) if item.get("quantity") is not None else None,
            latest_review.get("decision"), latest_review.get("reviewed_quantity"),
            item.get("layer"), item.get("status"), item.get("confidence_class"),
            ", ".join(item.get("evidence_handles", [])),
            "是" if item.get("requires_human_review", True) else "否",
            item.get("notice"),
        ])

    evidence_sheet = workbook.create_sheet("实体证据")
    evidence_sheet.append([
        "实体Handle", "图层", "实体类型", "测量类型", "原始值", "原始单位",
        "标准值", "标准单位", "计算公式", "可信等级", "警告",
    ])
    for item in report.get("measurements", []):
        evidence_sheet.append([
            item.get("entity_handle"), item.get("layer"), item.get("entity_type"),
            item.get("measurement_kind"), str(item.get("raw_value")),
            item.get("source_unit"), str(item.get("normalized_value"))
            if item.get("normalized_value") is not None else None,
            item.get("normalized_unit"), item.get("formula"),
            item.get("confidence_class"), "; ".join(item.get("warnings", [])),
        ])

    mapping_sheet = workbook.create_sheet("图层映射")
    mapping_sheet.append([
        "图层", "映射状态", "映射来源", "规则编号", "候选清单项",
        "专业", "计量类型", "单位", "实体类型检查", "实际实体类型", "说明",
    ])
    for suggestion in report.get("mapping_suggestions", []):
        candidates = suggestion.get("candidates", [])
        if not candidates:
            mapping_sheet.append([
                suggestion.get("layer"), suggestion.get("status"), None, None, None,
                None, None, None, None, None, suggestion.get("notice"),
            ])
        for rule in candidates:
            mapping_sheet.append([
                suggestion.get("layer"), suggestion.get("status"),
                suggestion.get("mapping_source"), rule.get("rule_id"),
                rule.get("item_name"), rule.get("discipline"),
                rule.get("measurement_kind"), rule.get("unit"),
                rule.get("entity_type_check"),
                ", ".join(rule.get("actual_entity_types", [])), rule.get("notes"),
            ])

    info_sheet = workbook.create_sheet("运行信息")
    info_sheet.append(["字段", "值"])
    for key, value in [
        ("项目ID", project_id), ("图纸ID", drawing_id), ("运行ID", run_id),
        ("任务", run["task"]), ("引擎", run["engine_id"]),
        ("原始文件", report.get("source_filename")),
        ("原始图纸SHA-256", report.get("source_drawing_sha256")),
        ("分析文件SHA-256", report.get("drawing_sha256")),
        ("图纸修订", report.get("drawing_revision")),
        ("源单位", report.get("source_unit")),
        ("单位状态", report.get("unit_status")),
        ("范围", report.get("scope")),
        ("警告", "; ".join(report.get("warnings", []))),
    ]:
        info_sheet.append([key, value])

    review_sheet = workbook.create_sheet("复核记录")
    review_sheet.append(["候选ID", "复核决定", "原始候选量", "复核后数量", "复核说明", "复核人", "时间"])
    for review in repository.list_drawing_candidate_reviews(run_id):
        review_sheet.append([
            review.get("candidate_id"),
            review.get("decision"),
            review.get("original_quantity"),
            review.get("reviewed_quantity"),
            review.get("note"),
            review.get("reviewer"),
            review.get("created_at"),
        ])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            longest = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[letter].width = min(max(longest + 2, 12), 56)

    with tempfile.SpooledTemporaryFile() as output:
        workbook.save(output)
        output.seek(0)
        content = output.read()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="auditos-takeoff-{run_id}.xlsx"'
        },
    )


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
