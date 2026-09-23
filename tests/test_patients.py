"""The REST API: CRUD, the response envelope, validation, auth, soft delete, duplicates."""

import uuid

from conftest import AUTH, PATIENT, create_patient


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "database": "ok"}, "error": None}


def test_create_stores_normalised_values(client):
    response = client.post(
        "/patients",
        json={**PATIENT, "phone_number": "(212) 555-0142", "state": "new york", "sex": "f",
              "last_name": "D O E", "date_of_birth": "April twelfth nineteen eighty five"},
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["error"] is None
    patient = body["data"]
    assert (patient["phone_number"], patient["state"], patient["sex"]) == ("2125550142", "NY", "Female")
    assert (patient["last_name"], patient["date_of_birth"]) == ("Doe", "1985-04-12")
    assert patient["preferred_language"] == "English"
    uuid.UUID(patient["patient_id"])


def test_validation_error_names_the_field(client):
    response = client.post("/patients", json={**PATIENT, "date_of_birth": "2099-01-01"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {
        "data": None,
        "error": {"message": "Date of birth cannot be in the future", "field": "date_of_birth"},
    }


def test_missing_field_and_bad_json(client):
    missing = client.post("/patients", json={"first_name": "Jane"}, headers=AUTH)
    assert missing.status_code == 422
    assert missing.json()["error"]["field"] == "last_name"

    bad = client.post("/patients", content="{not json", headers={**AUTH, "Content-Type": "application/json"})
    assert bad.status_code == 400
    assert bad.json()["error"]["message"] == "Request body is not valid JSON"


def test_writes_need_the_key_reads_do_not(client):
    assert client.post("/patients", json=PATIENT).status_code == 401
    assert client.post("/patients", json=PATIENT, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/patients", json=PATIENT, headers={"X-API-Key": "Bearer test-key"}).status_code == 201
    assert client.get("/patients").status_code == 200


def test_get_one(client):
    patient = create_patient(client)
    assert client.get(f"/patients/{patient['patient_id']}").json()["data"] == patient
    assert client.get(f"/patients/{uuid.uuid4()}").status_code == 404
    bad_id = client.get("/patients/not-a-uuid")
    assert bad_id.status_code == 422
    assert bad_id.json()["error"]["field"] == "patient_id"


def test_list_filters(client):
    create_patient(client)
    create_patient(client, first_name="Carlos", last_name="Rivera", phone_number="3055550187",
                   date_of_birth="1972-11-03")

    def names(query):
        return [p["first_name"] for p in client.get(f"/patients{query}").json()["data"]]

    assert names("") == ["Carlos", "Jane"]  # newest first
    assert names("?last_name=DOE") == ["Jane"]
    assert names("?phone_number=(305) 555-0187") == ["Carlos"]
    assert names("?date_of_birth=04/12/1985") == ["Jane"]


def test_partial_update(client):
    patient = create_patient(client, email="jane@example.com")
    url = f"/patients/{patient['patient_id']}"

    updated = client.put(url, json={"city": "Boston", "state": "MA"}, headers=AUTH).json()["data"]
    assert (updated["city"], updated["state"], updated["email"]) == ("Boston", "MA", "jane@example.com")

    cleared = client.put(url, json={"email": None}, headers=AUTH).json()["data"]
    assert cleared["email"] is None

    required = client.put(url, json={"last_name": None}, headers=AUTH)
    assert required.status_code == 422
    assert required.json()["error"] == {
        "message": "Last name is required and cannot be removed", "field": "last_name",
    }


def test_soft_delete(client):
    patient = create_patient(client)
    url = f"/patients/{patient['patient_id']}"

    deleted = client.delete(url, headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted_at"] is not None
    assert client.get(url).status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.delete(url, headers=AUTH).status_code == 404


def test_same_person_twice_returns_existing_record(client):
    first = create_patient(client)
    again = client.post(
        "/patients",
        json={**PATIENT, "first_name": "jane", "phone_number": "212-555-0142"},
        headers=AUTH,
    )
    assert again.status_code == 200
    assert again.json()["data"]["patient_id"] == first["patient_id"]


def test_family_member_on_the_same_number_is_a_new_patient(client):
    parent = create_patient(client)
    child = client.post(
        "/patients", json={**PATIENT, "first_name": "Lily", "date_of_birth": "2015-06-01"}, headers=AUTH
    )
    assert child.status_code == 201
    assert child.json()["data"]["patient_id"] != parent["patient_id"]
