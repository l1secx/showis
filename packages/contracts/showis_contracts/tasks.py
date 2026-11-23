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
    kind: Literal["name", "handle", "email", "phone", "domain", "ip"] = "name"
    first_name: str = ""
    last_name: str = ""
    nickname: str = ""
    email: str = ""
    phone: str = ""
    domain: str = ""
    ip: str = ""
    name_forms: list[str] = Field(default_factory=list)
    hints: dict[str, Any] = Field(default_factory=dict)
    review_required: bool = False

    @property
    def identifier(self) -> str:
        """Handle/e-posta/telefon/domain/IP için tek kimlik değeri."""
        return {
            "handle": (self.nickname or "").strip().lstrip("@").strip(),
            "email": (self.email or "").strip().lower(),
            "phone": (self.phone or "").strip(),
            "domain": (self.domain or "").strip().lower(),
            "ip": (self.ip or "").strip(),
        }.get(self.kind, "")

    @property
    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        if full:
            return full
        if self.kind == "handle":
            nick = (self.nickname or "").strip().lstrip("@").strip()
            return f"@{nick}" if nick else ""
        return self.identifier


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
    #: Tarama modu (O1/O2/O3). Site-check gibi pahalı connector'lar kapsamı
    #: moda göre daraltabilir; varsayılan eski davranışla uyumludur.
    mode: str = "O2"
