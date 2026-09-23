"""Call transcripts from Vapi's end-of-call reports, linked to the patient saved in the call."""

from conftest import AUTH, PATIENT, create_patient, end_of_call_report as report, tool_call


def test_call_is_linked_to_the_patient_it_saved(client):
    patient = tool_call(client, "save_patient", PATIENT, call_id="call_1")["data"]
    stored = client.post("/vapi/events", json=report("call_1"), headers=AUTH)
    assert stored.json() == {"data": {"stored": True}, "error": None}

    calls = client.get(f"/calls?patient_id={patient['patient_id']}").json()["data"]
    assert len(calls) == 1
    call = calls[0]
    assert (call["call_id"], call["patient_name"], call["caller_number"]) == ("call_1", "Jane Doe", "+12125550142")
    assert call["summary"] == "Jane Doe registered as a new patient."
    assert call["transcript"].startswith("AI: Hi, thanks for calling.")
    assert call["ended_reason"] == "assistant-ended-call"


def test_update_links_the_call_but_a_lookup_does_not(client):
    patient = create_patient(client)
    tool_call(client, "find_patient", {"phone_number": PATIENT["phone_number"]}, call_id="call_lookup")
    tool_call(client, "update_patient", {"patient_id": patient["patient_id"], "city": "Boston"}, call_id="call_update")
    client.post("/vapi/events", json=report("call_lookup"), headers=AUTH)
    client.post("/vapi/events", json=report("call_update"), headers=AUTH)

    linked = {c["call_id"]: c["patient_id"] for c in client.get("/calls").json()["data"]}
    assert linked == {"call_lookup": None, "call_update": patient["patient_id"]}


def test_dropped_call_is_still_recorded(client):
    client.post("/vapi/events", json=report("call_dropped", endedReason="customer-ended-call"), headers=AUTH)
    call = client.get("/calls").json()["data"][0]
    assert call["patient_id"] is None
    assert call["ended_reason"] == "customer-ended-call"


def test_older_payloads_with_top_level_transcript(client):
    legacy = {"message": {
        "type": "end-of-call-report", "call": {"id": "call_old"},
        "transcript": "AI: Hello", "summary": "Short call.",
    }}
    client.post("/vapi/events", json=legacy, headers=AUTH)
    call = client.get("/calls").json()["data"][0]
    assert (call["transcript"], call["summary"]) == ("AI: Hello", "Short call.")


def test_other_messages_are_ignored(client):
    response = client.post("/vapi/events", json={"message": {"type": "status-update", "call": {"id": "x"}}}, headers=AUTH)
    assert response.json()["data"] == {"stored": False}
    assert client.get("/calls").json()["data"] == []


def test_events_need_the_key(client):
    assert client.post("/vapi/events", json=report("call_1")).status_code == 401
