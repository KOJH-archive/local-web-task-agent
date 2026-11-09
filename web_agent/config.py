from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from .models import Settings


def load_settings(project_dir: str | Path, config_path: str | Path | None = None) -> Settings:
    root = Path(project_dir).resolve()
    values: dict[str, Any] = {}
    candidate = Path(config_path) if config_path else root / "config.json"
    if candidate.exists():
        values = json.loads(candidate.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(Settings)} - {"project_dir"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    settings = Settings(project_dir=root, **values)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    return settings

