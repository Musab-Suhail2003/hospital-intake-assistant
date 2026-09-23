"""Mock appointment scheduling: slot rules, booking, and conflicts."""

from datetime import datetime, timedelta, timezone

from conftest import AUTH, create_patient, tool_call
from scheduling import PRACTICE_TZ, bookable_days, is_bookable, open_slots, slots_on, spoken

# A Friday afternoon in September (practice time is UTC-4 then).
FRIDAY = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)


def test_bookable_days_start_tomorrow_and_skip_weekends():
    days = bookable_days(FRIDAY)
    assert days[0].isoformat() == "2026-09-28"  # Monday: Saturday and Sunday are skipped
    assert all(d.weekday() < 5 for d in days)
    assert days[-1] <= FRIDAY.date() + timedelta(days=14)


def test_slots_are_every_half_hour_from_nine_to_five():
    monday = bookable_days(FRIDAY)[0]
    local = [s.astimezone(PRACTICE_TZ) for s in slots_on(monday)]
    assert (local[0].hour, local[0].minute) == (9, 0)
    assert (local[-1].hour, local[-1].minute) == (16, 30)
    assert len(local) == 16


def test_spoken_label():
    assert spoken(datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)) == "Tuesday, September 29 at 9:30 AM"


def test_is_bookable():
    monday_nine = slots_on(bookable_days(FRIDAY)[0])[0]
    assert is_bookable(monday_nine, FRIDAY)
    assert not is_bookable(monday_nine + timedelta(minutes=15), FRIDAY)  # not on the half hour
    assert not is_bookable(FRIDAY + timedelta(hours=1), FRIDAY)           # today
    assert not is_bookable(monday_nine + timedelta(days=21), FRIDAY)      # too far ahead


def test_open_slots_filters():
    monday = bookable_days(FRIDAY)[0]
    first_two = slots_on(monday)[:2]
    free = open_slots(set(first_two), limit=3, now=FRIDAY)
    assert free == slots_on(monday)[2:5]  # booked times are skipped
    afternoon = open_slots(set(), day=monday, part_of_day="afternoon", now=FRIDAY)
    assert all(s.astimezone(PRACTICE_TZ).hour >= 12 for s in afternoon)
    saturday = monday - timedelta(days=2)
    assert open_slots(set(), day=saturday, now=FRIDAY) == []


def test_slots_endpoint(client):
    slots = client.get("/appointments/slots").json()["data"]
    assert 0 < len(slots) <= 6
    assert all(" at " in s["label"] for s in slots)
    mornings = client.get("/appointments/slots?part_of_day=morning").json()["data"]
    assert all(s["label"].endswith("AM") for s in mornings)
    bad = client.get("/appointments/slots?part_of_day=evening")
    assert bad.status_code == 422
    assert bad.json()["error"]["field"] == "part_of_day"


def test_booking_through_the_tool(client):
    patient = create_patient(client)
    slot = client.get("/appointments/slots").json()["data"][0]

    booked = tool_call(client, "book_appointment", {"patient_id": patient["patient_id"], "starts_at": slot["starts_at"]})
    assert booked["error"] is None
    assert booked["data"]["label"] == slot["label"]

    remaining = [s["starts_at"] for s in client.get("/appointments/slots").json()["data"]]
    assert slot["starts_at"] not in remaining
    listed = client.get(f"/appointments?patient_id={patient['patient_id']}").json()["data"]
    assert [a["label"] for a in listed] == [slot["label"]]


def test_booking_conflicts(client):
    jane = create_patient(client)
    carlos = create_patient(client, first_name="Carlos", phone_number="3055550187")
    first, second = client.get("/appointments/slots").json()["data"][:2]
    body = {"patient_id": jane["patient_id"], "starts_at": first["starts_at"]}
    assert client.post("/appointments", json=body, headers=AUTH).status_code == 201

    taken = client.post("/appointments", json={**body, "patient_id": carlos["patient_id"]}, headers=AUTH)
    assert taken.status_code == 409
    assert taken.json()["error"]["field"] == "starts_at"

    twice = client.post("/appointments", json={**body, "starts_at": second["starts_at"]}, headers=AUTH)
    assert twice.status_code == 409
    assert "already has an appointment" in twice.json()["error"]["message"]


def test_booking_validation(client):
    patient = create_patient(client)
    slot = client.get("/appointments/slots").json()["data"][0]
    off_grid = datetime.fromisoformat(slot["starts_at"]) + timedelta(minutes=10)

    not_a_slot = tool_call(client, "book_appointment", {"patient_id": patient["patient_id"], "starts_at": off_grid.isoformat()})
    assert not_a_slot["error"]["field"] == "starts_at"
    unknown = client.post("/appointments", json={"patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "starts_at": slot["starts_at"]}, headers=AUTH)
    assert unknown.status_code == 404
    assert client.post("/appointments", json={"patient_id": patient["patient_id"], "starts_at": slot["starts_at"]}).status_code == 401
