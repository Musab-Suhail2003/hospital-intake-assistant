"""Seed two demo patients through the API.

Usage:
    python seed.py                                          # local server, http://localhost:8000
    API_BASE_URL=https://your-app.up.railway.app python seed.py

Reads API_KEY from the environment or .env. Seeding goes through POST /patients, so the demo
records pass the same validation as real ones. Safe to re-run: the API returns the existing
record (200) instead of creating a duplicate.

The records are fictional: 555-01xx numbers are reserved for fiction, and the email address
uses example.com.
"""

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

SEED_PATIENTS = [
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "1985-04-12",
        "sex": "Female",
        "phone_number": "2125550142",
        "email": "jane.doe@example.com",
        "address_line_1": "350 Fifth Avenue",
        "address_line_2": "Apt 12B",
        "city": "New York",
        "state": "NY",
        "zip_code": "10118",
        "reason_for_visit": "Annual physical",
        "insurance_provider": "Aetna",
        "insurance_member_id": "W123456789",
        "emergency_contact_name": "John Doe",
        "emergency_contact_phone": "2125550143",
    },
    {
        "first_name": "Carlos",
        "last_name": "Rivera",
        "date_of_birth": "1972-11-03",
        "sex": "Male",
        "phone_number": "3055550187",
        "address_line_1": "1200 Brickell Avenue",
        "city": "Miami",
        "state": "FL",
        "zip_code": "33131",
        "reason_for_visit": "Blood pressure follow-up",
        "preferred_language": "Spanish",
    },
]


def main() -> int:
    load_dotenv()
    base_url = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("API_KEY")
    if not api_key:
        print("API_KEY is not set (environment or .env)", file=sys.stderr)
        return 1

    for patient in SEED_PATIENTS:
        name = f"{patient['first_name']} {patient['last_name']}"
        request = urllib.request.Request(
            f"{base_url}/patients",
            data=json.dumps(patient).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "User-Agent": "intake-seed/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                outcome = "created" if response.status == 201 else "already present"
                print(f"{name}: {outcome} ({json.load(response)['data']['patient_id']})")
        except urllib.error.HTTPError as exc:
            print(f"{name}: failed with {exc.code}: {exc.read().decode()}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
