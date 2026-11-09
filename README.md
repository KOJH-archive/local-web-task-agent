# Web task agent MVP

A local-first Windows web agent that accepts a natural-language command, plans it with installed Ollama models, operates sites through the official `ego-browser` task-space API, verifies the result, and records an audit trail in SQLite.

This is a separate project from AI Trend Hunter. Integration points are intentionally small: `TaskRunner` can be imported as a Python service, task results are JSON, and the SQLite schema keeps tasks, steps, approvals, and events separate.

## What works

- Natural-language planning with Ollama and JSON-constrained responses.
- Automatic model discovery from Ollama. Larger installed models are assigned to planning and verification; the smallest model is assigned to frequent navigation and safety work. Any role can be overridden in the UI, and generation retries other installed models after a failure.
- Direct HTTP/RSS retrieval for read-only collection when the planner selects it.
- Browser navigation through ego(lite): go to a URL, observe semantic snapshots, click, fill, press, select, extract, and traverse multiple pages.
- SQLite persistence for task history, steps, approvals, control state, results, errors, and audit events.
- Streamlit UI for commands, live steps/status, pause/resume/stop, one-time approvals, retry, model selection, task history, results, and audit logs.
- Read-only default, domain allowlist, step/time/repetition limits, prompt-injection detection, untrusted-content wrapping, and approval gates for consequential actions.

## Verified local environment

The project was built against:

- Windows on an LG Gram-class local machine.
- Python 3.14.x in `.venv`.
- Streamlit 1.67.0.
- `ego-browser` 0.6.2.7 with Chromium 156 and official usage guide v2.2.0.
- Ollama 0.40.15 with `qwen3.5:4b` and `gemma4:26b` discovered at build time.

The role router therefore defaults to `gemma4:26b` for planner/verifier and `qwen3.5:4b` for navigator/safety on this machine. This is not hard-coded; changing installed models changes the mapping.

## Install

Prerequisites:

1. Start Ollama and ensure `ollama list` shows at least one model.
2. Install ego lite and verify `ego-browser skill` ends with `[ego-browser:skill] end of guide`.
3. Install `uv`.

From PowerShell:

```powershell
cd web-task-agent
.\install.ps1
```

The installer creates `.venv`, installs the versions pinned in `uv.lock`, and copies `config.example.json` to `config.json` if one does not already exist.

## Run

The easiest Windows option is to double-click `start.bat`. On first run it installs the project automatically; later runs start the Streamlit interface directly.

Or run it from PowerShell:

```powershell
.\run.ps1
```

Open the local URL printed by Streamlit, enter a task, optionally limit allowed domains, and select **Run task**.

Diagnostics without the UI:

```powershell
.\.venv\Scripts\python.exe -m web_agent.cli --diagnostics --project-dir .
```

One-shot CLI task:

```powershell
.\.venv\Scripts\python.exe -m web_agent.cli "Open example.com and report the heading" --allow-domain example.com
```

## Safety model

Read-only mode is enabled by default. Every proposed browser action is re-evaluated immediately before execution.

The agent pauses for explicit one-time approval when an action may:

- publish, post, send, submit, delete, cancel, buy, order, subscribe, or pay;
- sign in or alter account/security state;
- upload, download, or change a file;
- leave a configured domain allowlist;
- click a likely commit control such as Save/Confirm while read-only mode is active;
- use an unknown action type.

Approving one step does not approve later steps. Denial stops the task. Pause and stop are polled between steps and while waiting for approval.

Web snapshots are prefixed as untrusted content before being sent to the model. Common prompt-injection patterns are logged, and the model prompts explicitly forbid following page instructions or disclosing secrets. This is defense in depth, not a formal security boundary: review approvals and use a narrow allowlist for sensitive sessions.

## ego(lite) integration and constraints

The adapter follows the installed official guide rather than Playwright assumptions:

- It runs JavaScript with `ego-browser nodejs`.
- On Windows it writes UTF-8 `.mjs` rounds and imports them through a one-line ASCII `-e` entrypoint.
- One persistent `TaskSpace` is used for the whole task and resumed by numeric `spaceId`.
- The first page is `p1`; snapshots provide refs used for later semantic actions.
- Only the documented `TaskSpace`, `Page`, `evaluate`, mouse, and keyboard surface is assumed.
- A successful verified task calls `task.finish({ keep: [] })` exactly once. Failed/stopped tasks are left available for diagnosis, matching the official guidance not to finish on error or user handoff.
- Every round is a new script file, which avoids replaying earlier actions.

Browser-owned permission prompts, CAPTCHA, device selection, MFA, and sites that block automation may still require user control. The current MVP records the block but does not yet expose ego's interactive `handOff()` flow inside Streamlit.

## Architecture

```text
Streamlit / CLI
      |
  TaskRunner
  |   |    |
  |   |    +-- SafetyPolicy -> SQLite approval queue
  |   +------- OllamaClient -> role routing + model fallback
  +----------- ReadOnlyFetcher (HTTP/RSS) or EgoBrowser (.mjs rounds)
                       |
                  page snapshot
                       |
                 navigator + verifier
```

Key modules:

- `web_agent/agent.py` — orchestration, limits, pause/stop, approval wait, verification.
- `web_agent/ego.py` — official ego-browser process and TaskSpace adapter.
- `web_agent/ollama.py` — installed-model discovery, role routing, JSON generation, fallback.
- `web_agent/safety.py` — domain, risk, read-only, and prompt-injection controls.
- `web_agent/database.py` — SQLite schema and audit persistence.
- `streamlit_app.py` — manual operating console.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The normal suite mocks process boundaries and does not navigate the web. The live ego(lite) test is opt-in:

```powershell
$env:RUN_EGO_E2E = "1"
.\.venv\Scripts\python.exe -m pytest -q tests\e2e_ego_example.py
```

## MVP limitations

- Local models can still choose poor selectors or stop too early. The verifier and loop limits reduce, but do not eliminate, this.
- Consequential-action detection is conservative keyword and action classification; it is not a complete semantic policy engine.
- Direct API discovery is planner-driven. A future connector registry can add known APIs, authentication, rate limits, and typed schemas.
- Worker threads are suitable for one local Streamlit process. Multi-user or crash-resilient operation should move workers to a separate queue/process.
- Authentication handoff, CAPTCHA, MFA, file chooser workflows, native downloads, and visual-only canvas interaction need dedicated UI flows.
- The SQLite database is local and unencrypted. Do not place secrets in commands.
