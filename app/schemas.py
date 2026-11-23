"""
Schemas = the "contracts" of the system.

Every piece of data that enters the system, comes out of the LLM, or goes into /
comes out of a tool is described here as a Pydantic model. If the data does not
match the contract, Pydantic raises a ValidationError and nothing runs.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---- shared vocabularies -------------------------------------------------
SystemName = Literal["crm", "finance_erp", "hr_portal", "prod_db"]
AccessLevel = Literal["read", "write", "admin"]
LEVEL_RANK = {"read": 1, "write": 2, "admin": 3}

USER_ID_PATTERN = r"^u\d{4}$"
REQUEST_ID_PATTERN = r"^REQ-[0-9a-f]{10}$"


class Strict(BaseModel):
    # extra="forbid": unknown fields are an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")


# ---- 1. Intake: what the client sends -----------------------------------
class AccessRequestInput(Strict):
    requester_id: str = Field(pattern=USER_ID_PATTERN, description="Who submits the request")
    target_user_id: str = Field(pattern=USER_ID_PATTERN, description="Who should receive access")
    request_text: str = Field(min_length=15, max_length=2000, description="Free-text request")
    external_ref: Optional[str] = Field(default=None, max_length=64, description="Ticket id")


# ---- 2. Understand: what the LLM must return -----------------------------
class Understanding(Strict):
    intent: Literal["grant_access", "other"]
    system: Optional[SystemName] = None
    access_level: Optional[AccessLevel] = None
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)  # the policy caps it; the schema only rejects nonsense
    justification: Optional[str] = Field(default=None, max_length=500)
    gaps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---- Tool inputs ----------------------------------------------------------
class LookupUserParams(Strict):
    user_id: str = Field(pattern=USER_ID_PATTERN)


class SearchPoliciesParams(Strict):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=5)


class ReadPolicyParams(Strict):
    system: SystemName


class GrantAccessParams(Strict):
    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    user_id: str = Field(pattern=USER_ID_PATTERN)
    system: SystemName
    access_level: Literal["read", "write"]  # admin can never even be expressed here
    duration_days: int = Field(ge=1, le=180)


# ---- Tool outputs (validated too: a tool can return garbage) --------------
class Grant(Strict):
    grant_id: str
    system: SystemName
    access_level: AccessLevel
    request_id: str
    granted_at: str
    expires_at: str


class UserRecord(Strict):
    user_id: str = Field(pattern=USER_ID_PATTERN)
    name: str = Field(min_length=1)
    department: str = Field(min_length=1)
    title: str
    manager_id: Optional[str]
    status: Literal["active", "disabled"]
    access: list[Grant]


class Policy(Strict):
    policy_id: str
    title: str
    system: str
    allowed_departments: list[str]
    max_access_level: AccessLevel
    default_duration_days: int
    max_duration_days: int
    text: str


class PolicyHit(Strict):
    policy_id: str
    title: str
    score: float
    snippet: str


# ---- 4. Plan --------------------------------------------------------------
class ActionPlan(Strict):
    tool: Literal["grant_access"]
    params: GrantAccessParams
    policy_refs: list[str]
    risk_level: Literal["low", "medium", "high"]
    summary: str
