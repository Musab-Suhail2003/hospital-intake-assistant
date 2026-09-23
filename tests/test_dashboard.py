"""The server-rendered dashboard."""

from conftest import AUTH, PATIENT, create_patient, end_of_call_report as report, tool_call


def test_lists_patients_appointments_and_calls(client):
    patient = tool_call(client, "save_patient", {**PATIENT, "reason_for_visit": "Annual physical"}, call_id="call_1")["data"]
    slot = client.get("/appointments/slots").json()["data"][0]
    client.post("/appointments", json={"patient_id": patient["patient_id"], "starts_at": slot["starts_at"]}, headers=AUTH)
    client.post("/vapi/events", json=report("call_1"), headers=AUTH)

    page = client.get("/dashboard")
    assert page.status_code == 200
    html = page.text
    assert "1 patient ·" in html
    assert "Jane Doe" in html and "(212) 555-0142" in html and "04/12/1985" in html
    assert slot["label"] in html                       # next appointment column
    assert "Jane Doe registered as a new patient." in html  # call summary
    assert "<details><summary>Show</summary>" in html        # transcript


def test_escapes_caller_text(client):
    create_patient(client, address_line_1="<script>alert(1)</script>", reason_for_visit='"><img src=x onerror=alert(2)>')
    html = client.get("/dashboard").text
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_filters_and_bad_filter(client):
    create_patient(client)
    create_patient(client, first_name="Carlos", last_name="Rivera", phone_number="3055550187")
    assert "1 matching patient" in client.get("/dashboard?last_name=rivera&phone_number=").text
    bad = client.get("/dashboard?date_of_birth=2099-01-01")
    assert bad.status_code == 422
    assert "Date of birth cannot be in the future" in bad.text


def test_database_down_shows_a_message(client, database_down):
    page = client.get("/dashboard")
    assert page.status_code == 500
    assert "reach the patient records system" in page.text
