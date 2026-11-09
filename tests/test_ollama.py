from web_agent.models import ModelInfo
from web_agent.ollama import OllamaClient, _parameter_billions


def test_parameter_size_parsing():
    assert _parameter_billions("qwen3.5:4b") == 4.0
    assert _parameter_billions("gemma4:26b") == 26.0


def test_role_routing_uses_fast_and_smart(monkeypatch):
    client = OllamaClient("http://127.0.0.1:11434")
    monkeypatch.setattr(
        client,
        "list_models",
        lambda: [ModelInfo("small:4b", parameter_billions=4), ModelInfo("large:26b", parameter_billions=26)],
    )
    roles = client.role_map()
    assert roles["navigator"] == "small:4b"
    assert roles["planner"] == "large:26b"
    assert roles["verifier"] == "large:26b"


def test_valid_override_is_respected(monkeypatch):
    client = OllamaClient("http://127.0.0.1:11434")
    monkeypatch.setattr(
        client,
        "list_models",
        lambda: [ModelInfo("a:4b", parameter_billions=4), ModelInfo("b:8b", parameter_billions=8)],
    )
    assert client.role_map({"planner": "a:4b"})["planner"] == "a:4b"

