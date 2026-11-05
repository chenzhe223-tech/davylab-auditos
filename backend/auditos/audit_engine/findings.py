from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

FindingStatus = Literal[
    "draft", "needs_evidence", "pending_review", "accepted",
    "rejected", "adjusted", "closed"
]


@dataclass(frozen=True)
class AuditFinding:
    code: str
    category: str
    title: str
    description: str
    source_ref: str | None
    rule_id: str
    rule_version: str
    declared_amount: Decimal | None = None
    calculated_amount: Decimal | None = None
    difference_amount: Decimal | None = None
    status: FindingStatus = "pending_review"
    requires_human_review: bool = True
