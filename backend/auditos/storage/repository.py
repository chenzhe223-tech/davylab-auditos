import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from auditos.domain.models import ProjectCreate, ProjectRead, utc_now


def database_path() -> Path:
    configured = os.getenv("AUDITOS_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / ".davylab" / "auditos"
    root.mkdir(parents=True, exist_ok=True)
    return root / "auditos.sqlite3"


class ProjectRepository:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    import_id TEXT NOT NULL REFERENCES imports(id),
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL REFERENCES findings(id),
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drawings (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    stored_path TEXT NOT NULL,
                    revision TEXT,
                    source_unit TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drawing_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    drawing_id TEXT NOT NULL REFERENCES drawings(id),
                    task TEXT NOT NULL,
                    engine_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_layer_mappings (
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    rule_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, rule_id)
                );
                CREATE TABLE IF NOT EXISTS project_layer_mapping_history (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    rule_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)



    def save_project_layer_mapping(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        rule_id = str(payload["rule_id"])
        now = utc_now().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version, created_at FROM project_layer_mappings "
                "WHERE project_id = ? AND rule_id = ?",
                (project_id, rule_id),
            ).fetchone()
            version = int(row["version"]) + 1 if row else 1
            created_at = row["created_at"] if row else now
            stored = {**payload, "version": version, "source": "project_custom", "status": "active"}
            serialized = json.dumps(stored, ensure_ascii=False)
            connection.execute(
                "INSERT INTO project_layer_mappings VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, rule_id) DO UPDATE SET "
                "version=excluded.version, payload=excluded.payload, updated_at=excluded.updated_at",
                (project_id, rule_id, version, serialized, created_at, now),
            )
            connection.execute(
                "INSERT INTO project_layer_mapping_history VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), project_id, rule_id, version, serialized, now),
            )
        return stored

    def list_project_layer_mappings(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM project_layer_mappings WHERE project_id = ? "
                "ORDER BY rule_id",
                (project_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_project_layer_mapping_history(
        self, project_id: str, rule_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT version, payload, created_at FROM project_layer_mapping_history "
                "WHERE project_id = ? AND rule_id = ? ORDER BY version DESC",
                (project_id, rule_id),
            ).fetchall()
        return [
            {
                "version": row["version"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def create_drawing(
        self,
        project_id: str,
        filename: str,
        sha256: str,
        size_bytes: int,
        stored_path: str,
        revision: str | None = None,
        source_unit: str | None = None,
    ) -> dict[str, Any]:
        drawing_id = str(uuid.uuid4())
        created_at = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO drawings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (drawing_id, project_id, filename, sha256, size_bytes, stored_path,
                 revision, source_unit, created_at),
            )
        return {
            "id": drawing_id, "project_id": project_id, "filename": filename,
            "sha256": sha256, "size_bytes": size_bytes, "revision": revision,
            "source_unit": source_unit, "created_at": created_at,
        }

    def get_drawing(self, drawing_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM drawings WHERE id = ?"
        params: tuple[Any, ...] = (drawing_id,)
        if project_id is not None:
            query += " AND project_id = ?"
            params = (drawing_id, project_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return dict(row) if row else None

    def list_drawings(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, project_id, filename, sha256, size_bytes, revision, source_unit, created_at "
                "FROM drawings WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_drawing_run(
        self,
        project_id: str,
        drawing_id: str,
        task: str,
        engine_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        created_at = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO drawing_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, project_id, drawing_id, task, engine_id,
                 json.dumps(report, ensure_ascii=False, default=str), created_at),
            )
        return {
            "id": run_id, "project_id": project_id, "drawing_id": drawing_id,
            "task": task, "engine_id": engine_id, "created_at": created_at,
            "report": report,
        }

    def list_drawing_runs(self, drawing_id: str, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM drawing_runs WHERE drawing_id = ? AND project_id = ? "
                "ORDER BY created_at DESC",
                (drawing_id, project_id),
            ).fetchall()
        return [
            {
                "id": row["id"], "project_id": row["project_id"],
                "drawing_id": row["drawing_id"], "task": row["task"],
                "engine_id": row["engine_id"], "report": json.loads(row["report_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def create_project(self, payload: ProjectCreate) -> ProjectRead:
        project_id = str(uuid.uuid4())
        created_at = utc_now()
        data = payload.model_dump()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, payload, created_at) VALUES (?, ?, ?, ?)",
                (project_id, payload.name, json.dumps(data, ensure_ascii=False), created_at.isoformat()),
            )
        return ProjectRead(id=project_id, created_at=created_at, **data)

    def list_projects(self) -> list[ProjectRead]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload, created_at FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [ProjectRead(id=row["id"], created_at=row["created_at"], **json.loads(row["payload"]))
                for row in rows]

    def get_project(self, project_id: str) -> ProjectRead | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, payload, created_at FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        return ProjectRead(id=row["id"], created_at=row["created_at"], **json.loads(row["payload"]))

    def create_import(self, project_id: str, filename: str, sha256: str, row_count: int) -> str:
        import_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?)",
                (import_id, project_id, filename, sha256, row_count, utc_now().isoformat()),
            )
        return import_id

    def save_findings(self, project_id: str, import_id: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now().isoformat()
        saved = []
        with self._connect() as connection:
            for finding in findings:
                finding_id = str(uuid.uuid4())
                status = finding.get("status", "pending_review")
                connection.execute(
                    "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (finding_id, project_id, import_id, json.dumps(finding, ensure_ascii=False),
                     status, now, now),
                )
                saved.append({"id": finding_id, **finding})
        return saved

    def list_findings(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload, status, created_at, updated_at FROM findings "
                "WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        return [{"id": row["id"], **json.loads(row["payload"]), "status": row["status"],
                 "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    def update_finding_status(self, finding_id: str, new_status: str, note: str | None) -> dict[str, Any] | None:
        allowed = {"needs_evidence", "pending_review", "accepted", "rejected", "adjusted", "closed"}
        if new_status not in allowed:
            raise ValueError("Unsupported review status")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, status FROM findings WHERE id = ?", (finding_id,)
            ).fetchone()
            if row is None:
                return None
            old_status = row["status"]
            now = utc_now().isoformat()
            connection.execute(
                "UPDATE findings SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, finding_id),
            )
            connection.execute(
                "INSERT INTO review_events VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), finding_id, old_status, new_status, note, now),
            )
        return {"id": finding_id, "old_status": old_status, "status": new_status,
                "note": note, "updated_at": now}

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM imports WHERE id = ?", (import_id,)
            ).fetchone()
        return dict(row) if row else None
