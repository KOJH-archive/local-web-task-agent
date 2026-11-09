from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from web_agent.agent import WORKERS, TaskRunner
from web_agent.config import load_settings
from web_agent.database import AgentDatabase
from web_agent.ego import EgoBrowser
from web_agent.ollama import OllamaClient


PROJECT_DIR = Path(__file__).resolve().parent
settings = load_settings(PROJECT_DIR)
db = AgentDatabase(settings.db_path)
runner = TaskRunner(settings, database=db)

st.set_page_config(page_title="Web task agent", page_icon=":material/travel_explore:", layout="wide")
st.session_state.setdefault("selected_task_id", None)


@st.cache_data(ttl=10, max_entries=1)
def load_models() -> tuple[list[str], dict[str, str], str]:
    try:
        client = OllamaClient(settings.ollama_url, settings.request_timeout_seconds)
        models = [item.name for item in client.list_models()]
        return models, client.role_map(), ""
    except Exception as exc:
        return [], {}, str(exc)


def set_control(task_id: int, control: str) -> None:
    db.update_task(task_id, control=control)


def start_task(command: str, read_only: bool, domains_text: str, overrides: dict[str, str]) -> int:
    domains = [item.strip() for item in domains_text.replace("\n", ",").split(",") if item.strip()]
    task_id = runner.create_task(command, read_only=read_only, allowed_domains=domains)
    WORKERS.start(runner, task_id, overrides)
    st.session_state.selected_task_id = task_id
    return task_id


models, automatic_roles, model_error = load_models()

with st.sidebar:
    st.header("Runtime")
    if models:
        st.success(f"Ollama ready · {len(models)} models")
        for role, model in automatic_roles.items():
            st.caption(f"{role}: `{model}`")
    else:
        st.error(model_error or "Ollama is unavailable", title="Ollama not ready")
    try:
        ego_status = EgoBrowser(settings.runtime_dir).check_installation()
        st.success(f"ego-browser ready · {ego_status['guide']}")
    except Exception as exc:
        st.error(str(exc), title="ego-browser not ready")

    st.divider()
    tasks = db.list_tasks(50)
    task_ids = [item["id"] for item in tasks]
    if task_ids:
        current = st.session_state.selected_task_id
        if current not in task_ids:
            current = task_ids[0]
        index = task_ids.index(current)
        selected = st.selectbox(
            "Task history",
            task_ids,
            index=index,
            format_func=lambda value: f"#{value} · {next(t['status'] for t in tasks if t['id'] == value)}",
            key="history_task",
        )
        st.session_state.selected_task_id = selected
    st.caption("All commands, actions, approvals, and results are stored locally in SQLite.")

st.title("Web task agent")
st.caption("Local Ollama planning with approval-gated ego(lite) browser control")

with st.container(border=True):
    st.subheader("New task")
    command = st.text_area(
        "What should the agent do?",
        placeholder="Example: Open example.com and report the page heading and destination URL.",
        height=110,
        key="new_command",
    )
    with st.container(horizontal=True, vertical_alignment="bottom"):
        read_only = st.toggle(
            "Read-only mode",
            value=settings.read_only_default,
            help="Consequential actions always pause for approval; read-only mode adds stricter checks.",
            key="read_only",
        )
        domains = st.text_input(
            "Allowed domains",
            value=", ".join(settings.allowed_domains),
            placeholder="example.com, docs.python.org (blank allows navigation with other safety checks)",
            key="allowed_domains",
        )

    overrides: dict[str, str] = {}
    model_box = st.expander("Model routing", on_change="rerun")
    if model_box.open and models:
        with model_box:
            cols = st.columns(4)
            for col, role in zip(cols, ("planner", "navigator", "verifier", "safety")):
                default = automatic_roles.get(role, models[0])
                overrides[role] = col.selectbox(
                    role.capitalize(), models, index=models.index(default), key=f"model_{role}"
                )

    can_start = bool(command.strip()) and bool(models)
    if st.button(
        "Run task",
        type="primary",
        icon=":material/play_arrow:",
        disabled=not can_start,
        width="stretch",
        key="run_task",
    ):
        task_id = start_task(command, read_only, domains, overrides)
        st.success(f"Task #{task_id} started")
        st.rerun()


@st.fragment(run_every="2s")
def task_panel(task_id: int) -> None:
    task = db.get_task(task_id)
    if not task:
        st.warning("Task no longer exists.")
        return

    st.header(f"Task #{task_id}")
    status_state = "complete" if task["status"] == "completed" else (
        "error" if task["status"] in {"failed", "stopped"} else "running"
    )
    with st.status(f"{task['status'].replace('_', ' ').title()}", state=status_state, type="compact"):
        st.write(task["command"])
        if task["error"]:
            st.error(task["error"])

    with st.container(horizontal=True):
        st.metric("Status", task["status"])
        st.metric("Steps", len(db.list_steps(task_id)))
        st.metric("Mode", "Read-only" if task["read_only"] else "Approval-gated")

    with st.container(horizontal=True):
        if task["status"] in {"running", "awaiting_approval"}:
            if st.button("Pause", icon=":material/pause:", key=f"pause_{task_id}"):
                set_control(task_id, "pause")
                st.rerun(scope="fragment")
        if task["status"] == "paused":
            if st.button("Resume", icon=":material/play_arrow:", key=f"resume_{task_id}"):
                set_control(task_id, "run")
                st.rerun(scope="fragment")
        if task["status"] not in {"completed", "failed", "stopped"}:
            if st.button("Stop", icon=":material/stop:", key=f"stop_{task_id}"):
                set_control(task_id, "stop")
                st.rerun(scope="fragment")
        if task["status"] in {"failed", "stopped"}:
            if st.button("Retry as new task", icon=":material/refresh:", key=f"retry_{task_id}"):
                new_id = runner.create_task(
                    task["command"], task["read_only"], task["allowed_domains"], retry_of=task_id
                )
                WORKERS.start(runner, new_id, task["selected_models"])
                st.session_state.selected_task_id = new_id
                st.rerun()

    approvals = db.list_approvals(task_id, pending_only=True)
    for approval in approvals:
        with st.container(border=True):
            st.warning(approval["reason"], title=f"Approval required · {approval['risk']} risk")
            st.code(json.dumps(approval["action"], ensure_ascii=False, indent=2), language="json")
            with st.container(horizontal=True):
                if st.button(
                    "Approve once", type="primary", icon=":material/check:", key=f"approve_{approval['id']}"
                ):
                    db.decide_approval(approval["id"], True)
                    st.rerun(scope="fragment")
                if st.button(
                    "Deny", icon=":material/close:", key=f"deny_{approval['id']}"
                ):
                    db.decide_approval(approval["id"], False)
                    st.rerun(scope="fragment")

    steps = db.list_steps(task_id)
    if steps:
        st.subheader("Execution timeline")
        for step in steps:
            state = "complete" if step["status"] == "completed" else (
                "error" if step["status"] == "failed" else "running"
            )
            label = f"Step {step['step_no']} · {step['kind']} · {step['status']}"
            with st.status(label, state=state, type="step"):
                if step["message"]:
                    st.write(step["message"])
                st.code(json.dumps(step["action"], ensure_ascii=False, indent=2), language="json")
                observation = step["observation"]
                if observation.get("url"):
                    st.caption(observation["url"])

    if task["result"]:
        st.subheader("Result")
        st.json(task["result"], expanded=True)

    audit = st.expander("Audit log", on_change="rerun")
    if audit.open:
        with audit:
            events = db.list_events(task_id)
            rows = [
                {
                    "time": event["created_at"],
                    "level": event["level"],
                    "event": event["event_type"],
                    "details": json.dumps(event["payload"], ensure_ascii=False),
                }
                for event in events
            ]
            st.dataframe(rows, alt="Chronological audit events for this task")


if st.session_state.selected_task_id:
    task_panel(int(st.session_state.selected_task_id))

