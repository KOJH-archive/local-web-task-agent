from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import BrowserObservation


RESULT_MARKER = "__WEB_TASK_AGENT_RESULT__"


class EgoBrowserError(RuntimeError):
    pass


class EgoBrowser:
    """Small adapter around the official ego-browser Node.js task-space API."""

    def __init__(self, runtime_dir: str | Path, timeout: int = 90):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def check_installation(self) -> dict[str, str]:
        version = subprocess.run(
            ["ego-browser", "--version"], capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        if version.returncode != 0:
            raise EgoBrowserError(version.stderr.strip() or "ego-browser is not installed")
        version_text = (version.stdout or version.stderr).strip()
        guide = subprocess.run(
            ["ego-browser", "skill"], capture_output=True, text=True, encoding="utf-8", timeout=30
        )
        guide_text = "\n".join(part for part in (guide.stdout, guide.stderr) if part)
        if guide.returncode != 0 or "[ego-browser:skill] end of guide" not in guide_text:
            raise EgoBrowserError("ego-browser skill guide is unavailable or truncated")
        return {"version": version_text, "guide": "complete"}

    def start(self, task_id: int, url: str) -> BrowserObservation:
        payload = {"name": f"web-task-agent-{task_id}", "url": url}
        script = f"""
const input = {json.dumps(payload, ensure_ascii=False)};
const task = await taskSpace(input.name);
const page = task.page('p1');
await page.goto(input.url, {{ timeout: 60000 }});
await page.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => {{}});
const result = {{
  space_id: task.spaceId,
  page_label: page.label,
  url: await page.url(),
  title: await page.title(),
  snapshot: await page.snapshot()
}};
console.log('{RESULT_MARKER}' + JSON.stringify(result));
"""
        return self._run_observation(task_id, "start", script)

    def act(
        self,
        task_id: int,
        round_no: int,
        space_id: int,
        page_label: str,
        action: dict[str, Any],
    ) -> BrowserObservation:
        payload = {"space_id": space_id, "page_label": page_label, "action": action}
        script = f"""
const input = {json.dumps(payload, ensure_ascii=False)};
const task = await taskSpace(input.space_id);
const page = task.page(input.page_label);
const action = input.action;
let extracted = null;
switch (action.type) {{
  case 'goto':
    await page.goto(action.url, {{ timeout: 60000 }});
    break;
  case 'click':
    await page.click(action.selector, {{ label: action.label || 'click selected element' }});
    break;
  case 'fill':
    await page.fill(action.selector, action.value ?? '');
    break;
  case 'press':
    await page.press(action.selector, action.key || 'Enter');
    break;
  case 'select':
    await page.selectOption(action.selector, action.value);
    break;
  case 'wait':
    await page.waitForTimeout(Math.min(Number(action.ms || 500), 5000));
    break;
  case 'snapshot':
    break;
  case 'extract':
    extracted = await page.evaluate((input) => {{
      const nodes = input.selector
        ? [...document.querySelectorAll(input.selector)]
        : [document.body];
      return nodes.slice(0, input.limit || 50).map((node) => ({{
        text: (node.innerText || node.textContent || '').trim().slice(0, 5000),
        href: node.href || node.querySelector?.('a')?.href || null
      }}));
    }}, {{ selector: action.selector || null, limit: action.limit || 50 }});
    break;
  default:
    throw new Error('Unsupported action type: ' + action.type);
}}
if (action.type !== 'snapshot' && action.type !== 'extract') {{
  await page.waitForLoadState('domcontentloaded', {{ timeout: 15000 }}).catch(() => {{}});
}}
const result = {{
  space_id: task.spaceId,
  page_label: page.label,
  url: await page.url(),
  title: await page.title(),
  snapshot: await page.snapshot(),
  extracted
}};
console.log('{RESULT_MARKER}' + JSON.stringify(result));
"""
        return self._run_observation(task_id, f"round-{round_no:03d}", script)

    def finish(self, task_id: int, space_id: int) -> None:
        payload = {"space_id": space_id}
        script = f"""
const input = {json.dumps(payload)};
const task = await taskSpace(input.space_id);
await task.finish({{ keep: [] }});
console.log('{RESULT_MARKER}' + JSON.stringify({{ finished: true, space_id: input.space_id }}));
"""
        self._run_script(task_id, "finish", script)

    def _run_observation(self, task_id: int, name: str, script: str) -> BrowserObservation:
        result, notice = self._run_script(task_id, name, script)
        return BrowserObservation(
            space_id=int(result["space_id"]),
            page_label=str(result["page_label"]),
            url=str(result.get("url", "")),
            title=str(result.get("title", "")),
            snapshot=str(result.get("snapshot", "")),
            notice=notice,
        )

    def _run_script(self, task_id: int, name: str, script: str) -> tuple[dict[str, Any], str]:
        task_dir = self.runtime_dir / f"task-{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / f"{name}.mjs"
        path.write_text(script.strip() + "\n", encoding="utf-8")
        uri = path.resolve().as_uri()
        entrypoint = f"await import('{uri}')"
        completed = subprocess.run(
            ["ego-browser", "nodejs", "-e", entrypoint],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        notice = "\n".join(
            line for line in output.splitlines() if line.startswith("[ego-browser:notice]")
        )
        if completed.returncode != 0:
            raise EgoBrowserError(output.strip() or f"ego-browser exited {completed.returncode}")
        # ego-browser may forward Node console output on stderr depending on the
        # installed Windows launcher, so parse the combined stream.
        marked = [line for line in output.splitlines() if line.startswith(RESULT_MARKER)]
        if not marked:
            raise EgoBrowserError("ego-browser returned no structured result\n" + output[-2000:])
        try:
            return json.loads(marked[-1][len(RESULT_MARKER) :]), notice
        except json.JSONDecodeError as exc:
            raise EgoBrowserError(f"Invalid ego-browser result: {exc}") from exc

    @staticmethod
    def observation_dict(observation: BrowserObservation) -> dict[str, Any]:
        data = asdict(observation)
        if len(data["snapshot"]) > 20_000:
            data["snapshot"] = data["snapshot"][:20_000] + "\n[truncated]"
        return data

