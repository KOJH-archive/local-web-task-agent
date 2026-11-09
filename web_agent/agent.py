from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import quote_plus, urlparse

from .database import AgentDatabase
from .ego import EgoBrowser
from .fetcher import ReadOnlyFetcher
from .models import AgentResult, BrowserObservation, Settings
from .ollama import OllamaClient
from .prompts import NAVIGATOR_SYSTEM, PLANNER_SYSTEM, SUMMARIZER_SYSTEM, VERIFIER_SYSTEM
from .safety import SafetyPolicy, sanitize_snapshot


TERMINAL = {"completed", "failed", "stopped"}


class TaskStopped(RuntimeError):
    pass


class TaskRunner:
    def __init__(
        self,
        settings: Settings,
        database: AgentDatabase | None = None,
        ollama: OllamaClient | None = None,
        browser: EgoBrowser | None = None,
        fetcher: ReadOnlyFetcher | None = None,
    ):
        self.settings = settings
        self.db = database or AgentDatabase(settings.db_path)
        self.ollama = ollama or OllamaClient(settings.ollama_url, settings.request_timeout_seconds)
        self.browser = browser or EgoBrowser(settings.runtime_dir)
        self.fetcher = fetcher or ReadOnlyFetcher(settings.request_timeout_seconds)

    def create_task(
        self,
        command: str,
        read_only: bool | None = None,
        allowed_domains: list[str] | None = None,
        retry_of: int | None = None,
    ) -> int:
        clean = command.strip()
        if not clean:
            raise ValueError("Command cannot be empty")
        return self.db.create_task(
            clean,
            self.settings.read_only_default if read_only is None else read_only,
            self.settings.allowed_domains if allowed_domains is None else allowed_domains,
            retry_of=retry_of,
        )

    def run(self, task_id: int, model_overrides: dict[str, str] | None = None) -> AgentResult:
        task = self.db.get_task(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found")
        started = time.monotonic()
        self.db.update_task(task_id, status="running", control="run", error="")
        try:
            role_map = self.ollama.role_map(model_overrides)
            self.db.update_task(task_id, selected_models_json=role_map)
            plan = self._plan(task["command"], role_map)
            self.db.update_task(task_id, plan_json=plan)
            self.db.add_step(task_id, 0, "plan", "completed", action=plan, message=plan.get("summary", ""))

            mode = str(plan.get("mode", "browser"))
            if mode in {"http_get", "rss_get"}:
                result = self._run_read_only(task, plan, role_map, started)
            else:
                result = self._run_browser(task, plan, role_map, started)
            self.db.update_task(task_id, status="completed", result_json=result)
            self.db.log_event(task_id, "info", "task_completed", {"summary": result.get("summary", "")})
            return AgentResult(task_id, "completed", result.get("summary", "Completed"), result)
        except TaskStopped as exc:
            self.db.update_task(task_id, status="stopped", error=str(exc))
            return AgentResult(task_id, "stopped", str(exc))
        except Exception as exc:
            self.db.update_task(task_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            self.db.log_event(task_id, "error", "task_failed", {"error": repr(exc)})
            return AgentResult(task_id, "failed", str(exc))

    def _plan(self, command: str, role_map: dict[str, str]) -> dict[str, Any]:
        prompt = json.dumps({"command": command}, ensure_ascii=False)
        plan = self.ollama.generate_json("planner", role_map, PLANNER_SYSTEM, prompt)
        mode = plan.get("mode")
        if mode not in {"browser", "http_get", "rss_get"}:
            plan["mode"] = "browser"
        start_url = str(plan.get("start_url", ""))
        parsed = urlparse(start_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            plan["start_url"] = "https://www.google.com/search?q=" + quote_plus(command)
            plan["mode"] = "browser"
        return plan

    def _run_read_only(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        role_map: dict[str, str],
        started: float,
    ) -> dict[str, Any]:
        self._checkpoint(task["id"], started)
        mode = plan["mode"]
        action = {"type": mode, "url": plan["start_url"], "reason": "Direct read-only retrieval"}
        self._authorize(task, 1, action)
        data = self.fetcher.rss(plan["start_url"]) if mode == "rss_get" else self.fetcher.get(plan["start_url"])
        self.db.add_step(task["id"], 1, mode, "completed", action=action, observation=data)
        summarized = self.ollama.generate_json(
            "verifier",
            role_map,
            SUMMARIZER_SYSTEM,
            json.dumps({"command": task["command"], "data": data}, ensure_ascii=False)[:60_000],
        )
        return {key: value for key, value in summarized.items() if key != "_model"}

    def _run_browser(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        role_map: dict[str, str],
        started: float,
    ) -> dict[str, Any]:
        start_action = {"type": "goto", "url": plan["start_url"], "reason": "Start task"}
        self._authorize(task, 1, start_action)
        observation = self.browser.start(task["id"], plan["start_url"])
        self._record_observation(task["id"], 1, start_action, observation)
        previous_signatures: list[str] = []

        for step_no in range(2, self.settings.max_steps + 1):
            self._checkpoint(task["id"], started)
            safe_snapshot, injection_findings = sanitize_snapshot(observation.snapshot)
            if injection_findings:
                self.db.log_event(
                    task["id"], "warning", "prompt_injection_detected", {"patterns": injection_findings}
                )
            navigator_input = {
                "command": task["command"],
                "plan": plan,
                "current_url": observation.url,
                "title": observation.title,
                "snapshot": safe_snapshot,
                "remaining_steps": self.settings.max_steps - step_no + 1,
            }
            action = self.ollama.generate_json(
                "navigator",
                role_map,
                NAVIGATOR_SYSTEM,
                json.dumps(navigator_input, ensure_ascii=False),
            )
            action.pop("_model", None)
            signature = json.dumps(action, sort_keys=True, ensure_ascii=False)
            previous_signatures.append(signature)
            if previous_signatures[-self.settings.max_same_action :].count(signature) >= self.settings.max_same_action:
                raise RuntimeError("Stopped after the same action was proposed repeatedly")

            if action.get("type") == "finish":
                verification = self._verify(task, plan, observation, action, role_map)
                self.db.add_step(
                    task["id"], step_no, "verify", "completed" if verification.get("passed") else "failed",
                    action=action, observation=verification, message=str(verification.get("reason", "")),
                )
                if verification.get("passed"):
                    self.browser.finish(task["id"], observation.space_id)
                    result = verification.get("result") or {}
                    result.setdefault("summary", action.get("summary", "Completed"))
                    return result
                continue

            self._authorize(task, step_no, action, observation.url)
            try:
                observation = self.browser.act(
                    task["id"], step_no, observation.space_id, observation.page_label, action
                )
                self._record_observation(task["id"], step_no, action, observation)
            except Exception as exc:
                self.db.add_step(
                    task["id"], step_no, "browser", "failed", action=action, message=str(exc)
                )
                self.db.log_event(
                    task["id"], "warning", "step_failed_replan", {"step": step_no, "error": repr(exc)}
                )
                observation = self.browser.act(
                    task["id"], step_no, observation.space_id, observation.page_label, {"type": "snapshot"}
                )
        raise RuntimeError(f"Step limit reached ({self.settings.max_steps})")

    def _verify(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        observation: BrowserObservation,
        proposed: dict[str, Any],
        role_map: dict[str, str],
    ) -> dict[str, Any]:
        safe_snapshot, _ = sanitize_snapshot(observation.snapshot)
        return self.ollama.generate_json(
            "verifier",
            role_map,
            VERIFIER_SYSTEM,
            json.dumps(
                {
                    "command": task["command"],
                    "success_criteria": plan.get("success_criteria", []),
                    "proposed_result": proposed,
                    "url": observation.url,
                    "title": observation.title,
                    "evidence": safe_snapshot,
                },
                ensure_ascii=False,
            ),
        )

    def _authorize(
        self, task: dict[str, Any], step_no: int, action: dict[str, Any], current_url: str = ""
    ) -> None:
        decision = SafetyPolicy(task["read_only"], task["allowed_domains"]).evaluate(action, current_url)
        self.db.log_event(task["id"], "info", "safety_decision", asdict(decision) | {"step": step_no})
        if decision.allowed:
            return
        if not decision.requires_approval:
            raise RuntimeError(decision.reason)
        approval_id = self.db.request_approval(
            task["id"], step_no, action, decision.risk, decision.reason
        )
        deadline = time.monotonic() + self.settings.approval_timeout_seconds
        while time.monotonic() < deadline:
            current = self.db.get_task(task["id"])
            if not current or current["control"] == "stop":
                raise TaskStopped("Stopped while waiting for approval")
            approvals = self.db.list_approvals(task["id"])
            approval = next(item for item in approvals if item["id"] == approval_id)
            if approval["status"] == "approved":
                self.db.update_task(task["id"], status="running")
                return
            if approval["status"] == "denied":
                raise TaskStopped("User denied a consequential action")
            time.sleep(1)
        raise TaskStopped("Approval request timed out")

    def _checkpoint(self, task_id: int, started: float) -> None:
        if time.monotonic() - started > self.settings.max_seconds:
            raise TaskStopped(f"Time limit reached ({self.settings.max_seconds}s)")
        while True:
            task = self.db.get_task(task_id)
            if not task or task["control"] == "stop":
                raise TaskStopped("Stopped by user")
            if task["control"] != "pause":
                if task["status"] == "paused":
                    self.db.update_task(task_id, status="running")
                return
            if task["status"] != "paused":
                self.db.update_task(task_id, status="paused")
            time.sleep(0.5)

    def _record_observation(
        self, task_id: int, step_no: int, action: dict[str, Any], observation: BrowserObservation
    ) -> None:
        self.db.add_step(
            task_id,
            step_no,
            "browser",
            "completed",
            action=action,
            observation=self.browser.observation_dict(observation),
            message=f"{observation.title} — {observation.url}",
        )
        if observation.notice:
            self.db.log_event(task_id, "warning", "ego_notice", {"message": observation.notice})


class WorkerRegistry:
    def __init__(self):
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self, runner: TaskRunner, task_id: int, model_overrides: dict[str, str] | None = None) -> None:
        with self._lock:
            existing = self._threads.get(task_id)
            if existing and existing.is_alive():
                return
            thread = threading.Thread(
                target=runner.run,
                args=(task_id, model_overrides),
                name=f"web-agent-task-{task_id}",
                daemon=True,
            )
            self._threads[task_id] = thread
            thread.start()

    def active(self, task_id: int) -> bool:
        thread = self._threads.get(task_id)
        return bool(thread and thread.is_alive())


WORKERS = WorkerRegistry()
