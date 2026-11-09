from web_agent.database import AgentDatabase


def test_task_step_approval_and_audit_roundtrip(tmp_path):
    db = AgentDatabase(tmp_path / "agent.db")
    task_id = db.create_task("Inspect example.com", True, ["example.com"])
    db.update_task(task_id, status="running", plan_json={"mode": "browser"})
    db.add_step(task_id, 1, "browser", "completed", {"type": "goto"}, {"url": "https://example.com"})
    approval_id = db.request_approval(
        task_id, 2, {"type": "click", "label": "Delete"}, "high", "Potential deletion"
    )
    db.decide_approval(approval_id, False)

    task = db.get_task(task_id)
    assert task is not None
    assert task["plan"] == {"mode": "browser"}
    assert task["allowed_domains"] == ["example.com"]
    assert db.list_steps(task_id)[0]["observation"]["url"] == "https://example.com"
    assert db.list_approvals(task_id)[0]["status"] == "denied"
    assert any(event["event_type"] == "approval_decided" for event in db.list_events(task_id))

