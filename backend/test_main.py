import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_app.db"
os.environ["LLM_API_KEYS"] = "fake_key_1,fake_key_2"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

from fastapi.testclient import TestClient
from main import app, init_db

init_db()
client = TestClient(app)


def mock_completion(**kwargs):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Paris"))],
        usage=SimpleNamespace(prompt_tokens=15, completion_tokens=3),
        _hidden_params={"cache_hit": False},
    )


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


@patch("main.litellm.completion", side_effect=mock_completion)
def test_chat_and_failover(mock_llm):
    res = client.post(
        "/chat",
        json={"session_id": "test-s1", "message": "Capital of France?"},
        headers={"X-Simulate-Failure": "key_1=429"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1


def test_all_keys_fail_503():
    res = client.post(
        "/chat",
        json={"session_id": "test-s2", "message": "Hello"},
        headers={"X-Simulate-Failure": "*=429"},
    )
    assert res.status_code == 503
    assert res.json() == {"detail": "all providers unavailable"}