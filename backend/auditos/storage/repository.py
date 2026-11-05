import json
import os
import sqlite3
import uuid
from pathlib import Path

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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

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
