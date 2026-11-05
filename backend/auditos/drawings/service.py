import hashlib
from pathlib import Path
from uuid import uuid4

from auditos.drawings.adapters import CadAiAgentAdapter, DwgReaderAdapter, DxfCivilAdapter
from auditos.drawings.models import EngineCapability, TakeoffRequest
from auditos.drawings.router import route_drawing_task


MAX_DRAWING_BYTES = 250 * 1024 * 1024
ALLOWED_EXTENSIONS = {".dwg", ".dxf"}


def drawing_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_drawing_file(path: Path) -> dict:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Only DWG and DXF are supported in this phase")
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size == 0:
        raise ValueError("Drawing file is empty")
    if size > MAX_DRAWING_BYTES:
        raise ValueError("Drawing exceeds 250 MiB limit")
    return {
        "drawing_id": str(uuid4()),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": size,
        "sha256": drawing_sha256(path),
    }


def engine_capabilities() -> list[EngineCapability]:
    return [
        DwgReaderAdapter().probe(),
        CadAiAgentAdapter().probe(),
        DxfCivilAdapter().probe(),
    ]


def plan_takeoff(request: TakeoffRequest) -> dict:
    decision = route_drawing_task(request)
    capabilities = {item.engine_id: item for item in engine_capabilities()}
    selected = [decision.primary_engine, *decision.secondary_engines]
    engine_status = [
        capabilities[item] for item in selected if item in capabilities
    ]
    return {
        "decision": decision,
        "engine_status": engine_status,
        "ready": bool(engine_status and capabilities[decision.primary_engine].installed),
        "notice": "当前返回的是引擎路由建议；只有安装并通过预检的引擎才能执行。算量候选必须人工复核。",
    }
