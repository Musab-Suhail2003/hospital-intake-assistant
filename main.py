import logging
import os
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from dashboard import render_dashboard
from db import create_tables, get_db
from models import Patient
from schemas import (
    Envelope,
    ErrorDetail,
    FindPatientArgs,
    PatientCreate,
    PatientFilters,
    PatientMatch,
    PatientOut,
    PatientUpdate,
    UpdatePatientArgs,
    VapiToolCall,
    VapiToolRequest,
    VapiToolResponse,
    VapiToolResult,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("intake")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    if not os.environ.get("API_KEY"):
        logger.warning("API_KEY is not set; all write requests will be refused")
    yield


app = FastAPI(title="Patient Intake API", lifespan=lifespan)

DbSession = Annotated[Session, Depends(get_db)]


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One line per request: method, path, status and time taken.

    Path only: query strings can carry names and phone numbers. Replaces Uvicorn's access
    log (disabled in the Dockerfile), which has no timing.
    """
    started = time.perf_counter()
    status_code = 500  # stays 500 if the handler raises
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s %d %.0fms", request.method, request.url.path, status_code, elapsed_ms)


# --- Error handling: every error goes out in the same envelope as success. ---

def error_response(status_code: int, message: str, field: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": {"message": message, "field": field}},
    )


_LOC_SOURCES = {"body", "query", "path", "header", "cookie"}


def describe_validation_error(errors: list[dict]) -> tuple[int, str, str | None]:
    """Reduce Pydantic errors to (status, message, field) for the first failing field
    only, so the voice agent re-prompts for one thing."""
    err = errors[0]
    loc = err["loc"]
    if loc and loc[0] in _LOC_SOURCES:
        loc = loc[1:]
    field = str(loc[0]) if loc else None

    if err["type"] == "json_invalid":
        return status.HTTP_400_BAD_REQUEST, "Request body is not valid JSON", None
    if field is None:
        message = "Request body is required" if err["type"] == "missing" else err["msg"]
        return status.HTTP_422_UNPROCESSABLE_CONTENT, message, None

    label = field.replace("_", " ").capitalize()
    if err["type"] == "value_error":
        # Our own validator messages; Pydantic prefixes them with "Value error, ".
        message = err["msg"].removeprefix("Value error, ")
    elif err["type"] in ("missing", "string_too_short") or err.get("input", "") is None:
        message = f"{label} is required"
    elif err["type"] == "string_too_long":
        message = f"{label} must be {err['ctx']['max_length']} characters or fewer"
    else:
        message = f"{label}: {err['msg']}"
    return status.HTTP_422_UNPROCESSABLE_CONTENT, message, field


def validation_error_response(errors: list[dict]) -> JSONResponse:
    return error_response(*describe_validation_error(errors))


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    return validation_error_response(exc.errors())


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    return validation_error_response(exc.errors())


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return error_response(exc.status_code, str(exc.detail))


# Worded so the voice agent can read it to the caller as-is.
DATABASE_ERROR_MESSAGE = (
    "We couldn't reach the patient records system. Please try again in a moment."
)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    logger.error("Database error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, DATABASE_ERROR_MESSAGE)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")


# --- Auth: shared secret on writes only; reads stay open. ---

def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("API_KEY")
    if not expected:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Server API key is not configured")
    # Vapi's Bearer Token credential prepends "Bearer " unless that toggle is switched off.
    if x_api_key is not None:
        x_api_key = x_api_key.removeprefix("Bearer ")
    if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key header")


# --- Routes ---

def active_patients():
    """Base query for every read and write: soft-deleted rows are never visible."""
    return select(Patient).where(Patient.deleted_at.is_(None))


def get_active_patient(db: Session, patient_id: UUID) -> Patient:
    patient = db.scalar(active_patients().where(Patient.patient_id == patient_id))
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


@app.get("/health", response_model=Envelope[dict])
def health(db: DbSession):
    db.execute(text("SELECT 1"))
    return Envelope(data={"status": "ok", "database": "ok"})


@app.get("/patients", response_model=Envelope[list[PatientOut]])
def list_patients(filters: Annotated[PatientFilters, Query()], db: DbSession):
    return Envelope(data=query_patients(db, filters))


def query_patients(db: Session, filters: PatientFilters) -> list[PatientOut]:
    """Active patients matching the filters, newest first. Shared by the API and dashboard."""
    query = active_patients().order_by(Patient.created_at.desc())
    if filters.last_name:
        query = query.where(func.lower(Patient.last_name) == filters.last_name.lower())
    if filters.date_of_birth:
        query = query.where(Patient.date_of_birth == filters.date_of_birth)
    if filters.phone_number:
        query = query.where(Patient.phone_number == filters.phone_number)
    return [PatientOut.model_validate(p) for p in db.scalars(query).all()]


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession):
    """Server-rendered table of active patients, filterable like GET /patients.

    An HTML page, so errors are shown on the page rather than as a JSON envelope.
    """
    # The filter form submits empty boxes as "", which means "no filter" here.
    raw = {
        name: value.strip()
        for name, value in request.query_params.items()
        if name in PatientFilters.model_fields and value.strip()
    }
    try:
        filters = PatientFilters.model_validate(raw)
    except ValidationError as exc:
        _, message, _ = describe_validation_error(exc.errors())
        return HTMLResponse(render_dashboard([], raw, error=message), status_code=422)
    try:
        patients = query_patients(db, filters)
    except SQLAlchemyError:
        logger.exception("Database error on GET /dashboard")
        return HTMLResponse(render_dashboard([], raw, error=DATABASE_ERROR_MESSAGE), status_code=500)
    return HTMLResponse(render_dashboard(patients, raw))


@app.get("/patients/{patient_id}", response_model=Envelope[PatientOut])
def get_patient(patient_id: UUID, db: DbSession):
    return Envelope(data=PatientOut.model_validate(get_active_patient(db, patient_id)))


@app.post(
    "/patients",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
)
def create_patient(payload: PatientCreate, db: DbSession, response: Response):
    """201 with the new record, or 200 with the existing one if already registered."""
    patient, created = register_patient(db, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return Envelope(data=patient)


def register_patient(db: Session, payload: PatientCreate) -> tuple[PatientOut, bool]:
    """Insert a new patient unless the same person is already registered.

    Returns the record and whether it was created. "Same person" means the same phone
    number, name and date of birth: phone alone isn't enough, because family members
    share numbers. This also makes a retried save_patient call harmless.
    """
    existing = db.scalar(
        active_patients().where(
            Patient.phone_number == payload.phone_number,
            func.lower(Patient.first_name) == payload.first_name.lower(),
            func.lower(Patient.last_name) == payload.last_name.lower(),
            Patient.date_of_birth == payload.date_of_birth,
        )
    )
    if existing is not None:
        out = PatientOut.model_validate(existing)
        logger.info("Patient already registered, returning existing: %s", out.model_dump_json())
        return out, False

    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    out = PatientOut.model_validate(patient)
    logger.info("Patient created: %s", out.model_dump_json())
    return out, True


@app.put(
    "/patients/{patient_id}",
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
)
def update_patient(patient_id: UUID, payload: PatientUpdate, db: DbSession):
    patient = get_active_patient(db, patient_id)
    return Envelope(data=apply_update(db, patient, payload.model_dump(exclude_unset=True)))


def apply_update(db: Session, patient: Patient, changes: dict) -> PatientOut:
    for field, value in changes.items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    out = PatientOut.model_validate(patient)
    logger.info("Patient updated: %s", out.model_dump_json())
    return out


@app.delete(
    "/patients/{patient_id}",
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
)
def delete_patient(patient_id: UUID, db: DbSession):
    patient = get_active_patient(db, patient_id)
    patient.deleted_at = func.now()
    db.commit()
    db.refresh(patient)
    out = PatientOut.model_validate(patient)
    logger.info("Patient soft-deleted: %s", out.model_dump_json())
    return Envelope(data=out)


# --- Vapi tool webhooks ---
#
# Vapi posts each tool call to that tool's URL below. Validation and database failures
# still answer 200 in Vapi's `results` format, so the agent always gets a result it can
# act on mid-call. Each `result` is our usual envelope as a JSON string: on failure the
# agent sees `error.message` and `error.field` and can re-prompt for just that field.

ToolHandler = Callable[[Session, dict], Envelope]


def answer_tool_calls(
    body: VapiToolRequest, db: Session, tool_name: str, handler: ToolHandler
) -> VapiToolResponse:
    results = [
        VapiToolResult(
            name=call.name,
            tool_call_id=call.id,
            result=run_tool_call(db, call, tool_name, handler).model_dump_json(),
        )
        for call in body.message.tool_call_list
    ]
    return VapiToolResponse(results=results)


def run_tool_call(db: Session, call: VapiToolCall, tool_name: str, handler: ToolHandler) -> Envelope:
    if call.name != tool_name:
        return Envelope(error=ErrorDetail(message=f"Unknown tool: {call.name}"))
    if call.arguments is None:
        return Envelope(error=ErrorDetail(message="Tool call arguments are not valid JSON"))
    try:
        return handler(db, call.arguments)
    except ValidationError as exc:
        _, message, field = describe_validation_error(exc.errors())
        logger.info("%s %s rejected: %s (field=%s)", call.name, call.id, message, field)
        return Envelope(error=ErrorDetail(message=message, field=field))
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error in %s %s", call.name, call.id)
        return Envelope(error=ErrorDetail(message=DATABASE_ERROR_MESSAGE))


@app.post(
    "/tools/save_patient",
    response_model=VapiToolResponse,
    dependencies=[Depends(require_api_key)],
)
def save_patient_tool(body: VapiToolRequest, db: DbSession):
    """Register the caller, once they have confirmed their details."""
    return answer_tool_calls(body, db, "save_patient", handle_save_patient)


def handle_save_patient(db: Session, arguments: dict) -> Envelope[PatientOut]:
    patient, _ = register_patient(db, PatientCreate.model_validate(arguments))
    return Envelope(data=patient)


@app.post(
    "/tools/find_patient",
    response_model=VapiToolResponse,
    dependencies=[Depends(require_api_key)],
)
def find_patient_tool(body: VapiToolRequest, db: DbSession):
    """Look up existing patients by phone number, so the agent can offer an update."""
    return answer_tool_calls(body, db, "find_patient", handle_find_patient)


def handle_find_patient(db: Session, arguments: dict) -> Envelope[list[PatientMatch]]:
    phone_number = FindPatientArgs.model_validate(arguments).phone_number
    patients = db.scalars(
        active_patients()
        .where(Patient.phone_number == phone_number)
        .order_by(Patient.created_at)
    ).all()
    return Envelope(data=[PatientMatch.model_validate(p) for p in patients])


@app.post(
    "/tools/update_patient",
    response_model=VapiToolResponse,
    dependencies=[Depends(require_api_key)],
)
def update_patient_tool(body: VapiToolRequest, db: DbSession):
    """Change a returning patient's details, once they have confirmed the changes."""
    return answer_tool_calls(body, db, "update_patient", handle_update_patient)


def handle_update_patient(db: Session, arguments: dict) -> Envelope[PatientOut]:
    # Models often send "" or null for fields they aren't changing. Treat those as not
    # sent, so an update made by voice can never clear a field by accident.
    sent = {
        key: value
        for key, value in arguments.items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }
    args = UpdatePatientArgs.model_validate(sent)
    patient = db.scalar(active_patients().where(Patient.patient_id == args.patient_id))
    if patient is None:
        logger.info("update_patient: patient %s not found", args.patient_id)
        return Envelope(error=ErrorDetail(message="Patient not found", field="patient_id"))
    changes = args.model_dump(exclude_unset=True, exclude={"patient_id"})
    return Envelope(data=apply_update(db, patient, changes))
