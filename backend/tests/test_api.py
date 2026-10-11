import json

import pytest
from fastapi.testclient import TestClient

from app.agent.models import IntentDecision, SQLDraft
from app.config import Settings
from app.main import create_app
from tests.conftest import decision, draft
from tests.fakes import ScriptedLLM


@pytest.fixture
def client(tmp_path, db_path):
    llm = ScriptedLLM(
        structured={IntentDecision: [decision("generate_sql")] * 5, SQLDraft: [draft("SELECT * FROM Departments")] * 5},
    )
    settings = Settings(database_path=db_path, checkpoint_path=tmp_path / "cp.db", static_dir=tmp_path / "none",
                        rate_limit_per_minute=3)
    with TestClient(create_app(settings, llm=llm)) as c:
        yield c


def _events(response):
    events, current = [], None
    for line in response.iter_lines():
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current:
            events.append((current, json.loads(line.split(":", 1)[1])))
    return events


def test_health_and_schema(client):
    assert client.get("/api/health").json()["llm_ready"] is True
    tables = {t["name"] for t in client.get("/api/schema").json()["tables"]}
    assert {"Employees", "Customers", "Orders"} <= tables


def test_stream_emits_steps_then_result(client):
    with client.stream("POST", "/api/chat/stream", json={"session_id": "session-01", "message": "departments"}) as r:
        events = _events(r)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "result" and "step" in kinds
    steps = [d["node"] for e, d in events if e == "step"]
    assert steps[:3] == ["guard_input", "classify_intent", "retrieve_schema"]
    assert events[-1][1]["results"]["row_count"] == 8


def test_json_endpoint_and_validation(client):
    assert client.post("/api/chat", json={"session_id": "session-02", "message": "departments"}).json()["type"] == "sql"
    assert client.post("/api/chat", json={"session_id": "bad id!", "message": "x"}).status_code == 422


def test_rate_limit(client):
    for _ in range(3):
        client.post("/api/chat", json={"session_id": "session-03", "message": "DROP TABLE x"})
    assert client.post("/api/chat", json={"session_id": "session-03", "message": "DROP TABLE x"}).status_code == 429


def test_missing_llm_returns_503(tmp_path, db_path):
    settings = Settings(_env_file=None, database_path=db_path, checkpoint_path=tmp_path / "cp.db",
                        static_dir=tmp_path / "none", llm_provider="anthropic", anthropic_api_key=None)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").json()["llm_ready"] is False
        assert c.post("/api/chat", json={"session_id": "session-04", "message": "hi"}).status_code == 503


def test_forwarded_for_header_cannot_bypass_rate_limit(client):
    codes = [client.post("/api/chat", json={"session_id": "session-05", "message": "DROP TABLE x"},
                         headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(5)]
    assert codes.count(429) == 2
