"""The Vapi tool endpoints: request shapes, results, and failures the caller can recover from."""

import json

from conftest import AUTH, PATIENT, create_patient, tool_call


def test_save_accepts_every_documented_request_shape(client):
    nested = tool_call(client, "save_patient", PATIENT)
    assert nested["error"] is None

    for tool_call_entry in (
        {"id": "tc_1", "name": "save_patient", "arguments": {**PATIENT, "first_name": "Ann"}},
        {"id": "tc_1", "name": "save_patient", "parameters": {**PATIENT, "first_name": "Bea"}},
        {"id": "tc_1", "function": {"name": "save_patient", "arguments": json.dumps({**PATIENT, "first_name": "Cy"})}},
    ):
        response = client.post("/tools/save_patient", json={"message": {"toolCallList": [tool_call_entry]}}, headers=AUTH)
        result = json.loads(response.json()["results"][0]["result"])
        assert result["error"] is None, result
    assert len(client.get("/patients").json()["data"]) == 4


def test_save_normalises_spoken_values(client):
    result = tool_call(client, "save_patient", {
        **PATIENT,
        "last_name": "O apostrophe B R I E N",
        "date_of_birth": "March fifteenth nineteen ninety",
        "sex": "I'd rather not say",
        "phone_number": "two one two five five five oh one four two",
        "state": "Texas",
        "zip_code": "seven eight seven oh one",
    })
    patient = result["data"]
    assert (patient["last_name"], patient["date_of_birth"], patient["sex"]) == ("O'Brien", "1990-03-15", "Decline to Answer")
    assert (patient["phone_number"], patient["state"], patient["zip_code"]) == ("2125550142", "TX", "78701")


def test_save_error_is_a_200_result_naming_the_field(client):
    result = tool_call(client, "save_patient", {**PATIENT, "phone_number": "555 1234"})
    assert result == {
        "data": None,
        "error": {"message": "Phone number must be a 10-digit US phone number", "field": "phone_number"},
    }


def test_bad_arguments_and_unknown_tools_get_error_results(client):
    response = client.post("/tools/save_patient", json={"message": {"toolCallList": [
        {"id": "a", "function": {"name": "save_patient", "arguments": "{not json"}},
        {"id": "b", "function": {"name": "delete_everything", "arguments": {}}},
    ]}}, headers=AUTH)
    results = [json.loads(r["result"])["error"]["message"] for r in response.json()["results"]]
    assert results == ["Tool call arguments are not valid JSON", "Unknown tool: delete_everything"]


def test_non_tool_messages_get_an_empty_reply(client):
    response = client.post("/tools/save_patient", json={"message": {"type": "status-update"}}, headers=AUTH)
    assert response.json() == {"results": []}


def test_tools_need_the_key(client):
    assert client.post("/tools/save_patient", json={"message": {}}).status_code == 401


def test_duplicate_save_returns_the_existing_record(client):
    first = tool_call(client, "save_patient", PATIENT)["data"]
    second = tool_call(client, "save_patient", PATIENT)["data"]
    assert first["patient_id"] == second["patient_id"]


def test_find_patient_returns_only_names(client):
    patient = create_patient(client)
    create_patient(client, first_name="Lily", date_of_birth="2015-06-01")

    matches = tool_call(client, "find_patient", {"phone_number": "two one two, five five five, oh one four two"})["data"]
    assert [m["first_name"] for m in matches] == ["Jane", "Lily"]  # oldest first
    assert set(matches[0]) == {"patient_id", "first_name", "last_name"}
    assert matches[0]["patient_id"] == patient["patient_id"]

    assert tool_call(client, "find_patient", {"phone_number": "9998887777"}) == {"data": [], "error": None}
    assert tool_call(client, "find_patient", {"phone_number": "555"})["error"]["field"] == "phone_number"


def test_update_patient_changes_only_what_was_sent(client):
    patient = create_patient(client, email="jane@example.com")
    result = tool_call(client, "update_patient", {
        "patient_id": patient["patient_id"], "city": "Boston", "state": "Massachusetts",
        "email": "", "zip_code": None,  # placeholders the model sent for fields it isn't changing
    })
    updated = result["data"]
    assert (updated["city"], updated["state"]) == ("Boston", "MA")
    assert (updated["email"], updated["zip_code"]) == ("jane@example.com", "10118")


def test_update_patient_errors(client):
    patient = create_patient(client)
    future = tool_call(client, "update_patient", {"patient_id": patient["patient_id"], "date_of_birth": "2099-01-01"})
    assert future["error"]["field"] == "date_of_birth"
    unknown = tool_call(client, "update_patient", {"patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "city": "X"})
    assert unknown["error"] == {"message": "Patient not found", "field": "patient_id"}
    missing = tool_call(client, "update_patient", {"city": "X"})
    assert missing["error"]["field"] == "patient_id"


def test_database_failure_is_spoken_not_silent(client, database_down):
    result = tool_call(client, "save_patient", PATIENT)
    assert result["error"]["message"].startswith("We couldn't reach the patient records system")

    rest = client.post("/patients", json=PATIENT, headers=AUTH)
    assert rest.status_code == 500
    assert rest.json()["error"]["message"].startswith("We couldn't reach the patient records system")
