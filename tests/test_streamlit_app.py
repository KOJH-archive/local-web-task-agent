from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from web_agent.models import ModelInfo


@pytest.mark.skip(reason="Streamlit 1.67 AppTest hangs on Windows Python 3.14; verify with the runtime agent API")
def test_app_loads_without_crashing():
    app = Path(__file__).parents[1] / "streamlit_app.py"
    with (
        patch(
            "web_agent.ollama.OllamaClient.list_models",
            return_value=[ModelInfo("test:4b", parameter_billions=4)],
        ),
        patch(
            "web_agent.ego.EgoBrowser.check_installation",
            return_value={"version": "test", "guide": "complete"},
        ),
    ):
        at = AppTest.from_file(app, default_timeout=15).run()
    assert not at.exception
    assert at.title[0].value == "Web task agent"
    assert at.button(key="run_task").disabled

