"""Retrace service layer: job store, isolated run directories, DWG conversion.

Local-first: jobs live in the AuditOS SQLite database; artifacts live in
AUDITOS_DATA_DIR/retrace_runs/<job_id>/. Every job keeps input hash, params,
evidence JSON and artifact hashes for traceability.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from auditos.domain.models import utc_now
from auditos.retrace.pipeline import CODE_VERSION, run

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def retrace_runs_root() -> Path:
    configured = os.getenv("AUDITOS_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / ".davylab" / "auditos"
    return root / "retrace_runs"


def find_oda_converter() -> str | None:
    """Locate ODA File Converter on common Windows install paths or PATH."""
    candidates = []
    for base in ("C:/Program Files/ODA", "C:/Program Files (x86)/ODA"):
        root = Path(base)
        if root.is_dir():
            candidates += sorted(root.glob("ODAFileConverter*/ODAFileConverter.exe"))
    if candidates:
        return str(candidates[-1])
    which = shutil.which("ODAFileConverter.exe") or shutil.which("ODAFileConverter")
    return which


class RetraceService:
    def __init__(self, db_path: Path | None = None, runs_root: Path | None = None) -> None:
        from auditos.storage.repository import database_path
        self.db_path = db_path or database_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_root = runs_root or retrace_runs_root()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS retrace_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    params TEXT NOT NULL,
                    evidence TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            columns = {r[1] for r in connection.execute(
                "PRAGMA table_info(retrace_jobs)")}
            for name in ("review_state", "review_note", "reviewer", "reviewed_at"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE retrace_jobs ADD COLUMN {name} TEXT")

    # -- review gate -------------------------------------------------------

    REVIEW_THRESHOLD_PCT = 2.0

    def evaluate_review(self, evidence: dict | None, params: dict) -> dict:
        """Deterministic review gate: compare worst anchor residual against
        threshold. Uncalibrated jobs (no anchors) always need human review."""
        if not evidence:
            return {"review_state": "needs-review",
                    "reason": "no evidence produced"}
        anchors = (evidence.get("residuals") or {}).get("anchors") or []
        if not anchors:
            return {"review_state": "needs-review",
                    "reason": "uncalibrated (no anchors declared)"}
        worst = max(anchors, key=lambda a: abs(a["residual_pct"]))
        if abs(worst["residual_pct"]) <= self.REVIEW_THRESHOLD_PCT:
            return {"review_state": "auto-pass",
                    "reason": f"worst anchor {worst['residual_pct']}% within "
                              f"±{self.REVIEW_THRESHOLD_PCT}%",
                    "worst_anchor": worst["name"]}
        return {"review_state": "needs-review",
                "reason": f"worst anchor {worst['name']} at "
                          f"{worst['residual_pct']}% exceeds "
                          f"±{self.REVIEW_THRESHOLD_PCT}%",
                "worst_anchor": worst["name"]}

    def review_job(self, job_id: str, decision: str, reviewer: str,
                   note: str = "") -> dict:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("job not found")
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")
        state = job.get("review_state")
        if state in ("approved", "rejected"):
            raise ValueError(f"job already {state}")
        if job["status"] != "done":
            raise ValueError("only done jobs can be reviewed")
        new_state = "approved" if decision == "approve" else "rejected"
        self._update(job_id, review_state=new_state, reviewer=reviewer or "anonymous",
                     review_note=note, reviewed_at=utc_now().isoformat())
        return self.get_job(job_id)  # type: ignore[return-value]

    # -- job lifecycle ---------------------------------------------------

    def create_job(self, image_bytes: bytes, filename: str, params: dict) -> dict:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"Unsupported image type {suffix or '(none)'}; "
                             f"allowed: {sorted(ALLOWED_SUFFIXES)}")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds 20 MiB limit")
        if not image_bytes:
            raise ValueError("Uploaded image is empty")

        job_id = str(uuid.uuid4())
        run_dir = self.runs_root / job_id
        run_dir.mkdir(parents=True, exist_ok=False)
        image_path = run_dir / f"input{suffix}"
        image_path.write_bytes(image_bytes)

        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO retrace_jobs (id, status, params, created_at, updated_at) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (job_id, json.dumps(params, ensure_ascii=False), now, now))

        self._execute(job_id, run_dir, image_path, params)
        return self.get_job(job_id)  # type: ignore[return-value]

    def _execute(self, job_id: str, run_dir: Path, image_path: Path, params: dict) -> None:
        try:
            dxf_path = run_dir / "retrace.dxf"
            png_path = run_dir / "preview.png" if params.get("emit_png", True) else None
            evidence = run(
                image_path, dxf_path,
                unit=params.get("unit", "unitless"),
                declare_mm_assumed=bool(params.get("declare_mm_assumed")),
                width_mm=params.get("width_mm"),
                height_mm=params.get("height_mm"),
                chain_mm=params.get("chain_mm"),
                emit_dims=bool(params.get("emit_dims")),
                labels=params.get("labels"),
                preview_png=png_path,
            )
            evidence["dwg"] = self._maybe_convert_dwg(run_dir, dxf_path,
                                                      bool(params.get("want_dwg")))
            evidence["review_gate"] = self.evaluate_review(evidence, params)
            self._update(job_id, status="done", evidence=evidence,
                         review_state=evidence["review_gate"]["review_state"])
        except Exception as exc:  # noqa: BLE001 - surface failure to the job record
            self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")

    def _maybe_convert_dwg(self, run_dir: Path, dxf_path: Path, want: bool) -> dict:
        if not want:
            return {"requested": False}
        oda = find_oda_converter()
        if not oda:
            return {"requested": True, "status": "unavailable",
                    "detail": "ODA File Converter not found on this machine"}
        out_dir = run_dir / "dwg"
        out_dir.mkdir(exist_ok=True)
        try:
            subprocess.run(
                [oda, str(dxf_path.parent), str(out_dir), "ACAD2018", "DWG", "0", "1"],
                check=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dwg_files = list(out_dir.glob("*.dwg"))
            if not dwg_files:
                return {"requested": True, "status": "failed",
                        "detail": "ODA produced no output"}
            dwg_path = dwg_files[0]
            return {"requested": True, "status": "converted",
                    "file": dwg_path.name,
                    "bytes": dwg_path.stat().st_size,
                    "note": "ODA encoding only; NOT an AutoCAD GUI acceptance"}
        except Exception as exc:  # noqa: BLE001
            return {"requested": True, "status": "failed",
                    "detail": f"{type(exc).__name__}: {exc}"}

    def _update(self, job_id: str, **fields) -> None:
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                  for v in fields.values()]
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE retrace_jobs SET {sets}, updated_at = ? WHERE id = ?",
                (*values, utc_now().isoformat(), job_id))

    # -- queries ----------------------------------------------------------

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrace_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._job_from_row(row)

    def list_jobs(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM retrace_jobs ORDER BY created_at DESC").fetchall()
        return [self._job_from_row(row) for row in rows]

    def _job_from_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "status": row["status"],
            "params": json.loads(row["params"]),
            "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
            "error": row["error"],
            "review_state": row["review_state"],
            "review_note": row["review_note"],
            "reviewer": row["reviewer"],
            "reviewed_at": row["reviewed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- artifacts ---------------------------------------------------------

    _ARTIFACTS = {
        "input": ("input.*", "application/octet-stream"),
        "dxf": ("retrace.dxf", "application/dxf"),
        "evidence": ("retrace.evidence.json", "application/json"),
        "png": ("preview.png", "image/png"),
    }

    def artifact_path(self, job_id: str, kind: str) -> Path | None:
        if kind == "dwg":
            pattern = "dwg/*.dwg"
        elif kind in self._ARTIFACTS:
            pattern = self._ARTIFACTS[kind][0]
        else:
            return None
        run_dir = self.runs_root / job_id
        matches = sorted(run_dir.glob(pattern))
        return matches[0] if matches else None

    def capabilities(self) -> dict:
        try:
            import cv2  # noqa: WPS433
            opencv = cv2.__version__
        except Exception:  # noqa: BLE001
            opencv = None
        return {
            "code_version": CODE_VERSION,
            "opencv": opencv,
            "oda_converter": find_oda_converter(),
            "max_image_bytes": MAX_IMAGE_BYTES,
            "allowed_suffixes": sorted(ALLOWED_SUFFIXES),
            "boundaries": [
                "no OCR / semantic recognition; text labels are human-entered",
                "renders/photos are negative-control inputs, not supported",
                "DWG via ODA is encoding-level, NOT AutoCAD GUI acceptance",
            ],
        }


_SERVICE: RetraceService | None = None


def get_service() -> RetraceService:
    """Lazy singleton so AUDITOS_DATA_DIR is honored at first use (test isolation)."""
    global _SERVICE  # noqa: WPS420
    if _SERVICE is None:
        _SERVICE = RetraceService()
    return _SERVICE
