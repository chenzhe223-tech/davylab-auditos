"""Verified subprocess adapters for optional upstream CAD engines."""
from __future__ import annotations

import importlib.util
import json
from importlib.metadata import PackageNotFoundError, version
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from auditos.drawings.geometry import analyze_dxf_geometry
from auditos.drawings.models import DrawingTask, EngineCapability, EngineId


class EngineAdapter:
    engine_id: EngineId

    def probe(self) -> EngineCapability:
        raise NotImplementedError

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


class NativeDxfGeometryAdapter(EngineAdapter):
    """AuditOS-owned deterministic geometry engine; authoritative calculation source."""

    engine_id = EngineId.NATIVE

    def probe(self) -> EngineCapability:
        installed = importlib.util.find_spec("ezdxf") is not None
        try:
            package_version = version("ezdxf") if installed else None
        except PackageNotFoundError:
            package_version = None
        return EngineCapability(
            engine_id=self.engine_id,
            installed=installed,
            available_tasks=[
                DrawingTask.BLOCK_COUNT,
                DrawingTask.LINEAR_TAKEOFF,
                DrawingTask.AREA_TAKEOFF,
            ],
            version=package_version,
            executable=None,
            warnings=[] if installed else ["未安装 ezdxf；原生 DXF 几何内核不可用。"],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict[str, Any]:
        capability = self.probe()
        if not capability.installed:
            raise RuntimeError("ezdxf is not installed")
        if task not in {
            DrawingTask.BLOCK_COUNT,
            DrawingTask.LINEAR_TAKEOFF,
            DrawingTask.AREA_TAKEOFF,
        }:
            raise ValueError(f"Native geometry engine does not support {task.value}")
        report = analyze_dxf_geometry(drawing_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "auditos-native-geometry.json"
        output_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return {
            "engine": self.engine_id.value,
            "engine_version": capability.version,
            "report": report,
            "output_file": str(output_file),
            "candidate_only": False,
            "note": (
                "确定性几何计算结果；仍需确认图层专业映射、图纸单位、图纸范围及安装工程计量规则。"
            ),
        }


class DwgReaderAdapter(EngineAdapter):
    """Adapter for dwg-reader CLI (read / quantity subcommands)."""

    engine_id = EngineId.DWG_READER

    def probe(self) -> EngineCapability:
        executable = shutil.which("dwg-reader")
        version = None
        warnings: list[str] = []
        if executable:
            try:
                result = self._run([executable, "--version"], timeout=10)
                if result.returncode == 0:
                    version = (result.stdout or result.stderr).strip()[:120]
            except (OSError, subprocess.TimeoutExpired):
                warnings.append("dwg-reader version probe failed.")
        else:
            warnings.append("未发现 dwg-reader CLI；安装上游包后可启用。")
        return EngineCapability(
            engine_id=self.engine_id,
            installed=bool(executable),
            available_tasks=[
                DrawingTask.STRUCTURE_SUMMARY,
                DrawingTask.TEXT_DIMENSIONS,
                DrawingTask.BLOCK_COUNT,
                DrawingTask.LINEAR_TAKEOFF,
                DrawingTask.AREA_TAKEOFF,
            ],
            version=version,
            executable=executable,
            warnings=warnings,
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict[str, Any]:
        capability = self.probe()
        if not capability.installed or not capability.executable:
            raise RuntimeError("dwg-reader engine is not installed")
        output_dir.mkdir(parents=True, exist_ok=True)
        source = Path(drawing_path)

        # Convert DWG into the isolated run directory. The upstream converter
        # writes <stem>.dxf there and returns a JSON conversion summary.
        if source.suffix.lower() == ".dwg":
            conversion = self._run([
                capability.executable, "convert", str(source), str(output_dir)
            ], timeout=600)
            if conversion.returncode != 0:
                raise RuntimeError(
                    f"DWG conversion failed ({conversion.returncode}): {conversion.stderr[-3000:]}"
                )
            converted = output_dir / f"{source.stem}.dxf"
            if not converted.is_file() or converted.stat().st_size == 0:
                raise RuntimeError("dwg-reader conversion did not produce a non-empty DXF.")
            source = converted
        elif source.suffix.lower() != ".dxf":
            raise ValueError("dwg-reader adapter accepts DWG or DXF only")

        if task in {DrawingTask.STRUCTURE_SUMMARY, DrawingTask.TEXT_DIMENSIONS}:
            command = [
                capability.executable, "read", str(source),
                "--format", "json", "--entities", "--no-convert",
            ]
            if task == DrawingTask.TEXT_DIMENSIONS:
                command.append("--dimensions")
            else:
                command.append("--components")
            result_file = None
        else:
            sections = "components" if task == DrawingTask.BLOCK_COUNT else "layers,dimensions"
            result_file = output_dir / f"dwg-reader-{task.value}.csv"
            command = [
                capability.executable, "quantity", str(source),
                "--output", str(result_file), "--sections", sections, "--no-convert",
            ]
        completed = self._run(command, timeout=600)
        if completed.returncode != 0:
            raise RuntimeError(
                f"dwg-reader failed ({completed.returncode}): {completed.stderr[-3000:]}"
            )
        return {
            "engine": self.engine_id.value,
            "engine_version": capability.version,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "converted_dxf": str(source) if source != drawing_path else None,
            "output_file": str(result_file) if result_file and result_file.is_file() else None,
            "candidate_only": True,
            "note": "DWG Reader output is a candidate; AuditOS must re-measure geometry and attach entity evidence.",
        }



class CadAiAgentAdapter(EngineAdapter):
    """Adapter for the upstream deterministic cad-analyze CLI.

    The upstream desktop app is not treated as a headless CLI. The stable
    analysis entry point is cad-analyze; its mock LLM is never used as evidence.
    """

    engine_id = EngineId.CAD_AI_AGENT

    def probe(self) -> EngineCapability:
        executable = shutil.which("cad-analyze")
        package_installed = importlib.util.find_spec("cad_dxf_agent") is not None
        available = bool(executable or package_installed)
        version = None
        if executable:
            try:
                result = self._run([executable, "--version"], timeout=10)
                if result.returncode == 0:
                    version = (result.stdout or result.stderr).strip()[:120]
            except (OSError, subprocess.TimeoutExpired):
                pass
        return EngineCapability(
            engine_id=self.engine_id,
            installed=available,
            available_tasks=[
                DrawingTask.COMPLEX_ANALYSIS,
                DrawingTask.AREA_TAKEOFF,
                DrawingTask.LINEAR_TAKEOFF,
                DrawingTask.BLOCK_COUNT,
                DrawingTask.STRUCTURE_SUMMARY,
            ],
            version=version,
            executable=executable or ("python -m cad_dxf_agent.cli.analyze" if package_installed else None),
            warnings=[] if available else [
                "未发现 cad-analyze；cad-dxf-agent 桌面程序本身不作为无头算量 CLI。"
            ],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict[str, Any]:
        capability = self.probe()
        if not capability.installed:
            raise RuntimeError("cad-dxf-agent analysis package is not installed")
        subcommand = {
            DrawingTask.AREA_TAKEOFF: "zones",
            DrawingTask.LINEAR_TAKEOFF: "takeoff",
            DrawingTask.BLOCK_COUNT: "takeoff",
            DrawingTask.STRUCTURE_SUMMARY: "summary",
            DrawingTask.COMPLEX_ANALYSIS: "summary",
        }.get(task)
        if subcommand is None:
            raise ValueError(f"cad-analyze does not expose a verified command for {task.value}")
        if capability.executable and capability.executable != "python -m cad_dxf_agent.cli.analyze":
            command = [capability.executable, subcommand, str(drawing_path), "--json"]
        else:
            command = [
                sys.executable, "-m", "cad_dxf_agent.cli.analyze",
                subcommand, str(drawing_path), "--json",
            ]
        completed = self._run(command)
        if completed.returncode != 0:
            raise RuntimeError(
                f"cad-analyze failed ({completed.returncode}): {completed.stderr[-3000:]}"
            )
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("cad-analyze did not return valid JSON.") from exc
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "engine": self.engine_id.value,
            "engine_version": capability.version,
            "command": command,
            "returncode": completed.returncode,
            "report": report,
            "candidate_only": True,
            "note": "Upstream deterministic analysis is a cross-check, not AuditOS settlement evidence.",
        }


class DxfCivilAdapter(EngineAdapter):
    """Adapter for documented dxf-civil scripts and their JSON stdout contract."""

    engine_id = EngineId.DXF_CIVIL

    def probe(self) -> EngineCapability:
        configured = os.getenv("AUDITOS_DXF_CIVIL_DIR")
        root = Path(configured).expanduser() if configured else None
        found = bool(root and (root / "scripts" / "summary.py").is_file())
        return EngineCapability(
            engine_id=self.engine_id,
            installed=found,
            available_tasks=[
                DrawingTask.STRUCTURE_SUMMARY,
                DrawingTask.VISUAL_REVIEW,
                DrawingTask.LINEAR_TAKEOFF,
                DrawingTask.AREA_TAKEOFF,
                DrawingTask.BLOCK_COUNT,
            ],
            executable=str(root) if found else None,
            warnings=[] if found else [
                "设置 AUDITOS_DXF_CIVIL_DIR 指向本地 dxf-civil 仓库目录后方可调用。"
            ],
        )

    def execute(self, task: DrawingTask, drawing_path: Path, output_dir: Path) -> dict[str, Any]:
        capability = self.probe()
        if not capability.installed or not capability.executable:
            raise RuntimeError("dxf-civil is not installed/configured")
        script_map = {
            DrawingTask.STRUCTURE_SUMMARY: "summary.py",
            DrawingTask.VISUAL_REVIEW: "render.py",
            DrawingTask.LINEAR_TAKEOFF: "takeoff.py",
            DrawingTask.AREA_TAKEOFF: "takeoff.py",
            DrawingTask.BLOCK_COUNT: "takeoff.py",
        }
        script_name = script_map.get(task)
        if script_name is None:
            raise ValueError(
                f"dxf-civil {task.value} requires additional structured input or is unsupported."
            )
        script = Path(capability.executable) / "scripts" / script_name
        if not script.is_file():
            raise RuntimeError(f"dxf-civil script not found: {script}")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [os.getenv("PYTHON", sys.executable), str(script), str(drawing_path)]
        if task == DrawingTask.VISUAL_REVIEW:
            preview = output_dir / "drawing-preview.png"
            command.extend(["--out", str(preview)])
        elif task in {DrawingTask.LINEAR_TAKEOFF, DrawingTask.AREA_TAKEOFF, DrawingTask.BLOCK_COUNT}:
            # Upstream takeoff returns JSON on stdout. Optional layer patterns
            # are deliberately not guessed here; all-layer output is a candidate.
            pass
        completed = self._run(command)
        if completed.returncode != 0:
            raise RuntimeError(
                f"dxf-civil {script_name} failed ({completed.returncode}): {completed.stderr[-3000:]}"
            )
        report: dict[str, Any] | None = None
        if task != DrawingTask.VISUAL_REVIEW:
            try:
                report = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"dxf-civil {script_name} did not return valid JSON.") from exc
        return {
            "engine": self.engine_id.value,
            "command": command,
            "returncode": completed.returncode,
            "report": report,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "output_file": str(preview) if task == DrawingTask.VISUAL_REVIEW and preview.is_file() else None,
            "confidence_class": "approximate" if task in {
                DrawingTask.LINEAR_TAKEOFF, DrawingTask.AREA_TAKEOFF, DrawingTask.BLOCK_COUNT
            } else None,
            "candidate_only": True,
            "note": (
                "dxf-civil takeoff is preliminary; HATCH area is a bounding-box proxy and "
                "polyline bulges are ignored by its length helper."
                if task in {DrawingTask.LINEAR_TAKEOFF, DrawingTask.AREA_TAKEOFF, DrawingTask.BLOCK_COUNT}
                else "Use for inspection/visual cross-check only."
            ),
        }
