from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import Platform


class TaskSpec(BaseModel):
    """Bir connector'a verilen iş birimi"""

    id: str
    connector: str
    platform: Platform
    kind: str  # connector'a özgü eylem
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = 5
    max_attempts: int = 3
    rate_limit_key: str | None = None


class Target(BaseModel):
    kind: Literal["name", "handle"] = "name"
    first_name: str = ""
    last_name: str = ""
    nickname: str = ""
    name_forms: list[str] = Field(default_factory=list)
    hints: dict[str, Any] = Field(default_factory=dict)
    review_required: bool = False

    @property
    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        if full:
            return full
        return f"@{self.nickname}" if self.nickname else ""


class Seed(BaseModel):
    platform: Platform
    url: str | None = None
    username: str | None = None
    email: str | None = None
    source: str = "manual"


class Budget(BaseModel):
    max_tasks: int = 300
    max_http_requests: int = 1500
    max_llm_calls: int = 20
    max_wall_seconds: int = 900
    max_cost_usd: float = 5.0


class ScanContext(BaseModel):
    case_id: str
    scan_id: str
    target: Target
    seeds: list[Seed] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    llm_session_id: str = ""
