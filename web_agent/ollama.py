from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from typing import Any

import requests

from .models import ModelInfo


PARAM_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*[bB](?:\b|[-_])")


def _parameter_billions(name: str, details: dict[str, Any] | None = None) -> float:
    details = details or {}
    text = " ".join(
        str(value)
        for value in (name, details.get("parameter_size", ""), details.get("family", ""))
    )
    match = PARAM_RE.search(text)
    return float(match.group("n")) if match else 0.0


class OllamaClient:
    def __init__(self, base_url: str, timeout: int = 45):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_models(self) -> list[ModelInfo]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = []
            for item in response.json().get("models", []):
                details = item.get("details") or {}
                models.append(
                    ModelInfo(
                        name=item["name"],
                        size=int(item.get("size") or 0),
                        parameter_billions=_parameter_billions(item["name"], details),
                    )
                )
            if models:
                return models
        except (requests.RequestException, ValueError, KeyError):
            pass

        completed = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, encoding="utf-8", timeout=5
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Ollama is installed but not reachable. Start Ollama, then retry. "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        models: list[ModelInfo] = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                models.append(ModelInfo(name=parts[0], parameter_billions=_parameter_billions(parts[0])))
        if not models:
            raise RuntimeError("No Ollama models are installed.")
        return models

    def role_map(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        models = self.list_models()
        ordered = sorted(models, key=lambda item: (item.parameter_billions, item.size, item.name))
        fast = ordered[0].name
        smart = ordered[-1].name
        mapping = {
            "planner": smart,
            "navigator": fast,
            "verifier": smart,
            "safety": fast,
        }
        available = {item.name for item in models}
        for role, name in (overrides or {}).items():
            if role in mapping and name in available:
                mapping[role] = name
        return mapping

    def generate_json(
        self,
        role: str,
        role_map: dict[str, str],
        system: str,
        prompt: str,
    ) -> dict[str, Any]:
        installed = [model.name for model in self.list_models()]
        preferred = role_map.get(role) or installed[0]
        candidates = [preferred, *[name for name in installed if name != preferred]]
        errors: list[str] = []
        for model in candidates:
            try:
                response = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "stream": False,
                        "format": "json",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "options": {"temperature": 0.1},
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("Model returned a non-object JSON value")
                parsed["_model"] = model
                return parsed
            except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append(f"{model}: {exc}")
        raise RuntimeError("All Ollama model attempts failed: " + " | ".join(errors))

    def diagnostics(self) -> dict[str, Any]:
        models = self.list_models()
        return {"models": [asdict(model) for model in models], "roles": self.role_map()}

