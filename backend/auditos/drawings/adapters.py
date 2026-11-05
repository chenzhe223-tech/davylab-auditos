import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

from auditos.drawings.models import DrawingTask, EngineCapability, EngineId


class EngineAdapter:
    engine_id: EngineId

    def probe(self) -> EngineCapability:
        raise NotImplementedError

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict:
        raise NotImplementedError


class DwgReaderAdapter(EngineAdapter):
    engine_id = EngineId.DWG_READER

    def probe(self) -> EngineCapability:
        executable = shutil.which("dwg-reader")
        available = [
            DrawingTask.STRUCTURE_SUMMARY, DrawingTask.TEXT_DIMENSIONS,
            DrawingTask.BLOCK_COUNT, DrawingTask.LINEAR_TAKEOFF,
        ]
        return EngineCapability(
            engine_id=self.engine_id, installed=bool(executable),
            available_tasks=available, executable=executable,
            warnings=[] if executable else ["未发现 dwg-reader CLI；请按上游说明安装可选引擎。"],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict:
        capability = self.probe()
        if not capability.installed:
            raise RuntimeError("dwg-reader engine is not installed")
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "dwg-reader-quantity.xlsx"
        command = [capability.executable or "dwg-reader", "quantity", str(drawing_path),
                   "-o", str(output)]
        if task == DrawingTask.BLOCK_COUNT:
            command.extend(["--sections", "components"])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        return {
            "engine": self.engine_id.value, "command": command,
            "returncode": completed.returncode, "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:], "output_file": str(output) if output.exists() else None,
        }


class CadAiAgentAdapter(EngineAdapter):
    engine_id = EngineId.CAD_AI_AGENT

    def probe(self) -> EngineCapability:
        executable = shutil.which("cad-dxf-agent")
        installed_package = importlib.util.find_spec("cad_dxf_agent") is not None
        found = bool(executable or installed_package)
        return EngineCapability(
            engine_id=self.engine_id, installed=found,
            available_tasks=[
                DrawingTask.COMPLEX_ANALYSIS, DrawingTask.AREA_TAKEOFF,
                DrawingTask.LINEAR_TAKEOFF, DrawingTask.SPATIAL_REVIEW,
                DrawingTask.VISUAL_REVIEW,
            ],
            executable=executable,
            warnings=[] if found else ["未发现 CAD AI Agent 安装；当前仅登记能力，未调用。"],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict:
        raise NotImplementedError(
            "CAD AI Agent requires a version-pinned integration contract; no unsafe generic CLI invocation."
        )


class DxfCivilAdapter(EngineAdapter):
    engine_id = EngineId.DXF_CIVIL

    def probe(self) -> EngineCapability:
        configured = os.getenv("AUDITOS_DXF_CIVIL_DIR")
        root = Path(configured).expanduser() if configured else None
        found = bool(root and (root / "scripts" / "summary.py").is_file())
        return EngineCapability(
            engine_id=self.engine_id, installed=found,
            available_tasks=[
                DrawingTask.STRUCTURE_SUMMARY, DrawingTask.SPATIAL_REVIEW,
                DrawingTask.VISUAL_REVIEW, DrawingTask.LINEAR_TAKEOFF,
                DrawingTask.AREA_TAKEOFF, DrawingTask.BLOCK_COUNT,
            ],
            executable=str(root) if found else None,
            warnings=[] if found else [
                "设置 AUDITOS_DXF_CIVIL_DIR 指向本地 dxf-civil 仓库目录后方可调用。"
            ],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict:
        capability = self.probe()
        if not capability.installed or not capability.executable:
            raise RuntimeError("dxf-civil is not installed/configured")
        script_map = {
            DrawingTask.STRUCTURE_SUMMARY: "summary.py",
            DrawingTask.SPATIAL_REVIEW: "spatial.py",
            DrawingTask.VISUAL_REVIEW: "render.py",
            DrawingTask.LINEAR_TAKEOFF: "takeoff.py",
            DrawingTask.AREA_TAKEOFF: "takeoff.py",
            DrawingTask.BLOCK_COUNT: "takeoff.py",
        }
        script = Path(capability.executable) / "scripts" / script_map[task]
        output_dir.mkdir(parents=True, exist_ok=True)
        if task == DrawingTask.VISUAL_REVIEW:
            output = output_dir / "drawing-preview.png"
            command = [os.getenv("PYTHON", "python"), str(script), str(drawing_path),
                       "--out", str(output)]
        elif task == DrawingTask.STRUCTURE_SUMMARY:
            output = output_dir / "drawing-summary.json"
            command = [os.getenv("PYTHON", "python"), str(script), str(drawing_path)]
        else:
            output = output_dir / "takeoff-output.txt"
            command = [os.getenv("PYTHON", "python"), str(script), str(drawing_path)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        return {
            "engine": self.engine_id.value, "command": command,
            "returncode": completed.returncode, "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:], "output_file": str(output) if output.exists() else None,
            "confidence_class": "approximate" if task == DrawingTask.AREA_TAKEOFF else None,
        }
