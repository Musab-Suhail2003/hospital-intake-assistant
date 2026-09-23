"""Shared test setup.

The tests run against a real Postgres, the same engine as production, and every table is
emptied before each test. Point TEST_DATABASE_URL at a throwaway database; by default it
is `intake_test` on the local Docker Postgres from the README.
"""

import json
import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/intake_test"
)
# Every test starts by emptying the tables, so refuse anything that isn't a test database.
assert "test" in TEST_DATABASE_URL.rsplit("/", 1)[-1], "TEST_DATABASE_URL must name a test database"

# Set before the app is imported: db.py reads DATABASE_URL at import time.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["API_KEY"] = "test-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from db import engine, get_db  # noqa: E402
from main import app  # noqa: E402

AUTH = {"X-API-Key": "test-key"}

PATIENT = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1985-04-12",
    "sex": "Female",
    "phone_number": "2125550142",
    "address_line_1": "350 Fifth Avenue",
    "city": "New York",
    "state": "NY",
    "zip_code": "10118",
}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # runs the app's startup, which creates the tables
        yield c


@pytest.fixture(autouse=True)
def empty_tables(client):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE calls, appointments, patients"))


@pytest.fixture
def database_down():
    """Point the app at a database that refuses connections, as if Postgres had died."""
    dead = sessionmaker(bind=create_engine(
        "postgresql://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 2}
    ))

    def broken_db():
        db = dead()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = broken_db
    yield
    app.dependency_overrides.clear()


def create_patient(client, **overrides) -> dict:
    response = client.post("/patients", json={**PATIENT, **overrides}, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def tool_call(client, name: str, arguments, call_id: str | None = None, path: str | None = None) -> dict:
    """Send one tool call the way Vapi does and return the envelope inside its result."""
    message = {
        "type": "tool-calls",
        "toolCallList": [{"id": "tc_1", "type": "function", "function": {"name": name, "arguments": arguments}}],
    }
    if call_id:
        message["call"] = {"id": call_id}
    response = client.post(f"/tools/{path or name}", json={"message": message}, headers=AUTH)
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["toolCallId"] == "tc_1"
    return json.loads(result["result"])


def end_of_call_report(call_id: str, **extra) -> dict:
    """An end-of-call report as Vapi sends it to /vapi/events."""
    return {"message": {
        "type": "end-of-call-report",
        "endedReason": "assistant-ended-call",
        "call": {"id": call_id, "customer": {"number": "+12125550142"}},
        "startedAt": "2026-09-24T10:00:00Z",
        "endedAt": "2026-09-24T10:03:00Z",
        "artifact": {"transcript": "AI: Hi, thanks for calling.\nUser: Hi, I'm Jane."},
        "analysis": {"summary": "Jane Doe registered as a new patient."},
        **extra,
    }}
