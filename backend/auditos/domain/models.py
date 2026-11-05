from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    specialty: str = Field(default="installation", pattern="^installation$")
    audit_stance: str = Field(
        default="owner_review",
        pattern="^(owner_review|contractor_self_check|third_party_consulting)$",
    )
    settlement_date: str | None = Field(default=None, max_length=20)
    contract_type: str | None = Field(default=None, max_length=80)


class ProjectRead(ProjectCreate):
    id: str
    created_at: datetime
