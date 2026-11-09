from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


TaskStatus = Literal[
    "queued", "running", "paused", "awaiting_approval", "completed", "failed", "stopped"
]


@dataclass(slots=True)
class Settings:
    project_dir: Path
    ollama_url: str = "http://127.0.0.1:11434"
    read_only_default: bool = True
    allowed_domains: list[str] = field(default_factory=list)
    max_steps: int = 20
    max_seconds: int = 600
    max_same_action: int = 3
    approval_timeout_seconds: int = 900
    request_timeout_seconds: int = 45

    @property
    def data_dir(self) -> Path:
        return self.project_dir / "data"

    @property
    def runtime_dir(self) -> Path:
        return self.project_dir / "runtime"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"


@dataclass(slots=True)
class ModelInfo:
    name: str
    size: int = 0
    parameter_billions: float = 0.0


@dataclass(slots=True)
class SafetyDecision:
    allowed: bool
    requires_approval: bool
    risk: Literal["low", "medium", "high"]
    reason: str


@dataclass(slots=True)
class BrowserObservation:
    space_id: int
    page_label: str
    url: str
    title: str
    snapshot: str
    notice: str = ""


@dataclass(slots=True)
class AgentResult:
    task_id: int
    status: TaskStatus
    summary: str
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

