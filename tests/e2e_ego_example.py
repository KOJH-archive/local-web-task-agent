"""Manual/opt-in live E2E: set RUN_EGO_E2E=1 before pytest."""

import os

import pytest

from web_agent.ego import EgoBrowser


@pytest.mark.skipif(os.environ.get("RUN_EGO_E2E") != "1", reason="live ego-browser E2E is opt-in")
def test_live_example_domain(tmp_path):
    browser = EgoBrowser(tmp_path)
    observation = browser.start(987654321, "https://example.com")
    assert "Example Domain" in observation.title
    assert "Example Domain" in observation.snapshot
    browser.finish(987654321, observation.space_id)

