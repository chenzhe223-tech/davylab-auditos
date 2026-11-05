from fastapi import APIRouter, HTTPException

from auditos.domain.models import ProjectCreate, ProjectRead
from auditos.storage.repository import ProjectRepository

router = APIRouter()
repository = ProjectRepository()


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
