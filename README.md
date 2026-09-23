# Voice AI Patient Registration

Call a US phone number, register as a new patient by talking to a voice agent, and find the
record in a REST API and a web dashboard. Call again from the same number and the agent
recognises you and offers to update your details instead.

| | |
|---|---|
| **Phone number** | **+1 (216) 755-7602** |
| **API base URL** | https://hospital-intake-assistant-production.up.railway.app |
| Dashboard | https://hospital-intake-assistant-production.up.railway.app/dashboard |
| Interactive API docs | https://hospital-intake-assistant-production.up.railway.app/docs |

Reads (`GET`) are open. Writes need an `X-API-Key` header; the key is provided separately
with the submission, not in this repository.

```bash
BASE=https://hospital-intake-assistant-production.up.railway.app
curl "$BASE/patients"                              # everyone registered
curl "$BASE/patients?last_name=doe"                # filter: last_name, date_of_birth, phone_number
curl "$BASE/patients/<patient_id>"                 # one patient
curl -X PUT "$BASE/patients/<patient_id>" -H "X-API-Key: $KEY" \
     -H "Content-Type: application/json" -d '{"city": "Boston"}'
```

---

## What a call looks like

1. The agent greets the caller and asks for their name, and for a spelling of the last name.
2. It offers the number the caller is ringing from as their phone number, then looks it up.
   If a patient already has that number, it says "It looks like we already have a record
   for Jane Doe. Would you like to update your information instead?"
3. It collects date of birth, sex, home address and the reason for the visit, checking each
   answer as it's given.
4. It offers once to take email, insurance, an emergency contact and preferred language.
5. It reads everything back in short chunks and waits for a yes.
6. It saves the record through the API, says "You're all set, Jane", and hangs up.

| The brief asks | What happens |
|---|---|
| Invalid date of birth or phone number | The agent re-asks for that field straight away. If one slips through, the API rejects it, naming the field, and the agent re-asks for just that detail. |
| Corrections ("it's D-A-V-I-S, not D-A-V-I-E-S") | Only that field changes; it's read back, and the call carries on |
| Caller wants to start over | Everything collected is discarded and the agent starts again from the name |
| Database write fails | The caller hears "We couldn't reach the patient records system…", the agent retries once, then asks them to call back later. It never claims success without a saved record. |
| Call drops mid-way | Nothing is saved until the caller confirms, so there's no half-finished record |
| Out-of-order answers, interruptions | Details given early are kept and not asked again; the agent stops when interrupted |
| Returning caller (bonus) | Recognised by phone number, as above |
| "Hablo español" (bonus) | The rest of the call is in Spanish, and Spanish is stored as the preferred language |
| Dashboard (bonus) | `/dashboard`, refreshes every 30 seconds |

---

## Architecture

```
 Caller ──phone──▶  Vapi: telephony, speech-to-text (Soniox), LLM (gpt-4.1-mini), text-to-speech
                        │
                        │  tool calls over HTTPS, with X-API-Key
                        ▼
                    FastAPI app on Railway (Docker)
                      /tools/find_patient  /tools/save_patient  /tools/update_patient
                      /patients (REST)     /dashboard           /health
                        │
                        │  SQLAlchemy
                        ▼
                    PostgreSQL on Railway
```

**Separation of concerns.** Vapi owns the phone line, speech and the conversation loop. The
backend owns validation and storage, and the agent never touches the database. The tool
endpoints Vapi calls use the same validation models and the same save and update functions
as the REST API, so a patient saved by voice and one saved with `curl` go through identical
rules.

| File | Role |
|---|---|
| `main.py` | Routes, the response envelope and error handling, API-key check, request logging, Vapi tool endpoints |
| `schemas.py` | Every validation rule, as Pydantic models (none in the route handlers) |
| `normalise.py` | Turns spoken input into clean values: "five five five…" → `555…`, "March fifteenth nineteen ninety" → `1990-03-15` |
| `models.py` | The `patients` table |
| `db.py` | Database connection, sessions, table creation |
| `dashboard.py` | The dashboard's HTML |
| `seed.py` | Adds two demo patients through the API |
| `vapi/` | Reference copy of the voice agent: [prompt and settings, with notes](vapi/system_prompt.md), and [tool definitions](vapi/tools.json) |

### Tech stack and why

| Choice | Why |
|---|---|
| **Vapi** | Telephony, speech-to-text, text-to-speech and the LLM loop in one managed service, so the time went into the prompt, tools and backend. The phone number is provisioned by Vapi. |
| **gpt-4.1-mini** | Quick enough for spoken turns, and reliable at calling tools with structured arguments |
| **Python 3.11, FastAPI** | Request validation built in, automatic interactive docs at `/docs`, little boilerplate |
| **Pydantic v2** | One place for every rule: types, formats and the speech parsing all live in the models |
| **SQLAlchemy 2** | Typed models and composable queries; one base query excludes soft-deleted rows everywhere |
| **PostgreSQL** | Real `DATE`, `UUID` and timezone-aware timestamp types, plus constraints the database enforces itself |
| **Railway** | Always-on service (no sleeping free tier, so a reviewer's call never waits for a cold start), managed Postgres in the same project with `DATABASE_URL` injected, and it deploys the repo's Dockerfile |
| **Docker** | The image tested locally is the one that runs in production |

---

## API

Every response, success or error, uses one envelope:

```json
{"data": {...}, "error": null}
{"data": null, "error": {"message": "Date of birth cannot be in the future", "field": "date_of_birth"}}
```

Validation errors name the field that failed, and only the first one, so the voice agent
re-asks for one thing at a time.

| Method | Path | Notes |
|---|---|---|
| GET | `/patients` | Active patients, newest first. Filters: `?last_name=` (ignores case), `?date_of_birth=`, `?phone_number=` (any format) |
| GET | `/patients/{patient_id}` | 404 if missing or soft-deleted |
| POST | `/patients` | 201 with the new record. If the same person (phone, name and date of birth) is already registered: 200 with the existing record. Needs the key. |
| PUT | `/patients/{patient_id}` | Partial update: only the fields sent change. Needs the key. |
| DELETE | `/patients/{patient_id}` | Soft delete: sets `deleted_at`, never removes the row. Needs the key. |
| GET | `/health` | 200 when the app can reach the database |
| GET | `/dashboard` | HTML table of patients, with the same filters |
| POST | `/tools/save_patient`, `/tools/find_patient`, `/tools/update_patient` | Called by Vapi (see below). Need the key. |

Status codes: 200, 201, 400 (body isn't valid JSON), 401 (missing or wrong key), 404, 422
(validation, with `field`), 500 (database or unexpected error, with a message that can be
read to a caller).

**Tool endpoints.** Vapi sends tool calls in its own wrapper and only accepts replies as
`{"results": [{"toolCallId": ..., "result": "<string>"}]}`. So these endpoints are the one
exception to the envelope: the envelope travels as a JSON string inside `result`. They also
answer 200 for validation and database failures, so the agent always gets a result it can
act on mid-call.

### Data model

One `patients` table with the brief's fields, plus one addition, `reason_for_visit`
(optional, up to 200 characters), because the agent asks why the caller is coming in.

- `patient_id` is a UUID, so IDs in URLs can't be guessed or counted.
- `date_of_birth` is a `DATE`; timestamps are `timestamptz`, set by the database, in UTC.
- `sex` has a `CHECK` constraint on the four allowed values rather than a native enum, which
  would be hard to change without migrations.
- Phone numbers are stored as 10 digits and ZIP codes as text, so leading zeros survive.
- Required fields are `NOT NULL`. Column lengths match the validation limits, so an
  over-long value gets a clear 422, not a database error.
- `phone_number` and `last_name` are indexed, since both are lookup keys.
- `phone_number` is deliberately not unique: family members share numbers.

### Voice input: liberal in, strict out

The agent is asked for clean formats, but whatever arrives is parsed on the server, since
the brief says never to trust the agent's validation.

| Field | Accepted | Stored |
|---|---|---|
| Phone | `(555) 123-4567`, `+1 555…`, "five five five one two three…", "double five" | `5551234567` |
| Date of birth | `1990-03-15`, `03/15/1990`, `March 15th, 1990`, "March fifteenth nineteen ninety" | `1990-03-15` |
| Name | "D A V I S", "D-A-V-I-S", "O apostrophe B R I E N" | `Davis`, `O'Brien` |
| Sex | "male", "F", "I'd rather not say" | `Male`, `Female`, `Decline to Answer` |
| State | "California", "C A", "Washington D.C." | `CA`, `DC` |
| ZIP | "nine oh two one oh", `902101234` | `90210`, `90210-1234` |
| Email | "john dot smith at gmail dot com" | `john.smith@gmail.com` |

---

## Setup

### Environment variables

| Variable | Needed by | Purpose |
|---|---|---|
| `DATABASE_URL` | App | Postgres connection string. Railway injects it; its `postgres://` prefix is handled. |
| `API_KEY` | App | Shared secret for writes, sent as `X-API-Key`. Without it, every write is refused. |
| `PORT` | App | Port to listen on; defaults to 8000 |
| `API_BASE_URL` | `seed.py` | Where to seed; defaults to `http://localhost:8000` |
| `VAPI_API_KEY`, `RAILWAY_TOKEN` | Nobody at runtime | Only for configuring Vapi and deploying from the command line |

### Run locally

Needs Python 3.11 and Docker (for Postgres).

```bash
docker run -d --name intake-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=intake \
  -p 5432:5432 postgres:16-alpine
cp .env.example .env                  # the DATABASE_URL in it matches the container above; set API_KEY
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload             # creates the table on first start
python seed.py                        # optional: two demo patients
```

Then open http://localhost:8000/docs or http://localhost:8000/dashboard.

To run the production image instead:
`docker build -t intake . && docker run --rm --env-file .env --network host intake`
(`--network host` lets the container reach Postgres on `localhost`; on macOS or Windows,
point `DATABASE_URL` at `host.docker.internal` and use `-p 8000:8000` instead).

### Deploy

1. Create a Railway project from this repository, and add the Postgres add-on (it injects
   `DATABASE_URL`).
2. Set `API_KEY` on the service. Railway builds the Dockerfile.
3. The table is created on first start. Columns added later are added on startup too (see
   limitations).
4. Optionally seed: `API_BASE_URL=https://<your-app> python seed.py`.

### Connect the voice agent

1. Create the three function tools in [`vapi/tools.json`](vapi/tools.json), replacing the
   URL host with your deployment and `<API_KEY>` with your key.
2. Create an assistant with the settings, first message and system prompt in
   [`vapi/system_prompt.md`](vapi/system_prompt.md), and attach the tools.
3. Attach the assistant to a Vapi phone number.

---

## Observability

- **Every request** gets one log line: method, path, status and time taken. Query strings
  are left out, because they can contain names and phone numbers.
- **Every patient write** (create, update, soft delete) logs the full record, as the brief
  requires. A registration that matches an existing patient logs "Patient already
  registered" with that record.
- **Every rejected tool call** logs the tool, the call ID, the error and the field, which
  shows where callers get stuck.
- Database errors log a full traceback. Callers and clients only ever see a plain message.

On Railway these appear in the service's logs. Vapi keeps its own call logs and transcripts.

---

## Known limitations and trade-offs

- **Reads and the dashboard are open.** Anyone with the URL can see every patient. The API
  key only protects writes. Fine for a demo with no real data (the brief rules out HIPAA);
  the first thing to lock down for real use.
- **No identity check on updates.** A caller who gives a number on file can update that
  patient. A real system would verify something only the patient knows, such as the date of
  birth, on the server.
- **Duplicate means same phone, name and date of birth.** The same person with a misheard
  date of birth, or a new number, is registered twice. Phone alone can't be the rule,
  because families share numbers.
- **Names allow more than the brief's rule.** The brief says letters, hyphens and
  apostrophes. The API also allows accented letters and single spaces, because "Mary Ann",
  "De La Cruz" and "José" would otherwise be rejected and send the agent into a loop.
  Digits and other symbols are still rejected.
- **Spelled-out names are capitalised simply:** "M C D O N A L D" becomes "Mcdonald".
- **All-numeric dates are month-first.** `15/03/1990` is rejected rather than swapped.
  Two-digit years become the most recent year that isn't in the future. "Twenty five" as a
  year is rejected as ambiguous.
- **A field can't be cleared by voice.** `update_patient` ignores blank values so that a
  model's empty placeholders never wipe data; clearing needs `PUT` with `null`.
- **No migrations framework.** Tables are created on startup. The one column added after
  launch, `reason_for_visit`, is added by an idempotent `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` on startup. That covers adding nullable columns, not changing or removing them.
- **Tool endpoints bend the envelope rule,** as described under [API](#api), because Vapi
  dictates the reply format.
- **Patient details appear in logs** (the brief asks for the payload to be logged). Real
  use would need masking or restricted log access.
- **No automated test suite.** Each stage was tested with scripted checks against the
  production Docker image and a real Postgres (validation paths, speech parsing, duplicate
  handling, database outages), then again against the live deployment. Those checks aren't
  in the repository yet.

---

## Next Steps

1. **Automated tests:** turn the scripted checks into a pytest suite for the API, the tool
   endpoints and the speech parsing, and run it in CI.
2. **Authentication on reads and the dashboard,** and masking patient details in logs.
3. **Server-side identity check** before `update_patient` changes anything.
4. **Call transcripts:** store Vapi's end-of-call reports linked to the patient, which also
   records calls that dropped before saving.
5. **Alembic migrations** in place of create-on-startup.
6. **Pagination** on `GET /patients` and the dashboard.
7. **Appointment scheduling** after registration (a bonus in the brief).
8. **Database hardening:** a `statement_timeout`, a functional index for case-insensitive
   name search, and partial indexes that skip soft-deleted rows.
