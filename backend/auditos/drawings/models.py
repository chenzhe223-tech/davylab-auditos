from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class EngineId(StrEnum):
    DWG_READER = "dwg_reader"
    CAD_AI_AGENT = "cad_ai_agent"
    DXF_CIVIL = "dxf_civil"
    NATIVE = "auditos_native"


class DrawingTask(StrEnum):
    STRUCTURE_SUMMARY = "structure_summary"
    TEXT_DIMENSIONS = "text_dimensions"
    BLOCK_COUNT = "block_count"
    LINEAR_TAKEOFF = "linear_takeoff"
    AREA_TAKEOFF = "area_takeoff"
    SPATIAL_REVIEW = "spatial_review"
    VISUAL_REVIEW = "visual_review"
    COMPLEX_ANALYSIS = "complex_analysis"


class ConfidenceClass(StrEnum):
    EXACT_GEOMETRY = "exact_geometry"
    ANNOTATION = "annotation"
    BLOCK_COUNT = "block_count"
    APPROXIMATE = "approximate"
    INFERRED = "inferred"


class MeasurementEvidence(BaseModel):
    project_id: str
    drawing_id: str
    drawing_revision: str | None = None
    drawing_sha256: str
    engine_id: EngineId
    engine_version: str | None = None
    adapter_version: str = "0.1.0"
    run_id: str
    source_sheet: str | None = None
    source_layout: str | None = None
    source_layer: str | None = None
    entity_handle: str | None = None
    entity_type: str | None = None
    block_name: str | None = None
    source_text: str | None = None
    dimension_text: str | None = None
    source_unit: str | None = None
    normalized_unit: str | None = None
    coordinate_scale: Decimal | None = None
    measurement_method: str
    raw_value: Decimal
    normalized_value: Decimal
    formula: str
    confidence_class: ConfidenceClass
    warning_codes: list[str] = Field(default_factory=list)
    unresolved_assumptions: list[str] = Field(default_factory=list)
    created_at: datetime


class EngineCapability(BaseModel):
    engine_id: EngineId
    installed: bool
    available_tasks: list[DrawingTask]
    version: str | None = None
    executable: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DrawingRouteDecision(BaseModel):
    task: DrawingTask
    primary_engine: EngineId
    secondary_engines: list[EngineId] = Field(default_factory=list)
    reasons: list[str]
    safety_constraints: list[str]
    requires_visual_review: bool = False
    requires_human_review: bool = True


class TakeoffRequest(BaseModel):
    filename: str
    task_description: str = Field(min_length=3, max_length=2000)
    discipline: str = Field(default="installation", max_length=80)
    measurement_kind: str = Field(default="auto", max_length=80)
    drawing_revision: str | None = Field(default=None, max_length=80)
    source_unit: str | None = Field(default=None, max_length=40)
    target_unit: str | None = Field(default=None, max_length=40)


class TakeoffCandidate(BaseModel):
    name: str
    unit: str
    quantity: Decimal
    source_layer: str | None = None
    source_entity_refs: list[str] = Field(default_factory=list)
    method: str
    confidence_class: ConfidenceClass
    warnings: list[str] = Field(default_factory=list)
    formula: str



class DrawingAnalysisRequest(BaseModel):
    task_description: str = Field(min_length=3, max_length=2000)
    measurement_kind: str = Field(default="auto", max_length=80)
    discipline: str = Field(default="all", max_length=40)
    layer_patterns: list[str] = Field(default_factory=list, max_length=100)
    source_unit_override: str | None = Field(default=None, max_length=40)



class ProjectLayerMapping(BaseModel):
    rule_id: str = Field(min_length=3, max_length=120)
    discipline: str = Field(min_length=2, max_length=40)
    item_name: str = Field(min_length=2, max_length=120)
    measurement_kind: str = Field(pattern="^(length|area|count)$")
    unit: str = Field(min_length=1, max_length=20)
    layer_patterns: list[str] = Field(min_length=1, max_length=50)
    entity_types: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=1000)
