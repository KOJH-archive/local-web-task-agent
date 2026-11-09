import json
import subprocess

from web_agent.ego import EgoBrowser, RESULT_MARKER


def test_start_uses_official_task_space_runner(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        script_path = next(tmp_path.rglob("start.mjs"))
        captured["script"] = script_path.read_text(encoding="utf-8")
        result = {
            "space_id": 7,
            "page_label": "p1",
            "url": "https://example.com",
            "title": "Example Domain",
            "snapshot": "heading Example Domain",
        }
        return subprocess.CompletedProcess(command, 0, RESULT_MARKER + json.dumps(result) + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    browser = EgoBrowser(tmp_path)
    observation = browser.start(1, "https://example.com")
    assert captured["command"][:3] == ["ego-browser", "nodejs", "-e"]
    assert "taskSpace(input.name)" in captured["script"]
    assert "page.snapshot()" in captured["script"]
    assert observation.title == "Example Domain"

