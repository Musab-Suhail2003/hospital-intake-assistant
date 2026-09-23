import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import create_tables, get_db
from models import Patient
from schemas import (
    Envelope,
    ErrorDetail,
    PatientCreate,
    PatientFilters,
    PatientOut,
    PatientUpdate,
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
    query = active_patients().order_by(Patient.created_at.desc())
    if filters.last_name:
        query = query.where(func.lower(Patient.last_name) == filters.last_name.lower())
    if filters.date_of_birth:
        query = query.where(Patient.date_of_birth == filters.date_of_birth)
    if filters.phone_number:
        query = query.where(Patient.phone_number == filters.phone_number)
    patients = db.scalars(query).all()
    return Envelope(data=[PatientOut.model_validate(p) for p in patients])


@app.get("/patients/{patient_id}", response_model=Envelope[PatientOut])
def get_patient(patient_id: UUID, db: DbSession):
    return Envelope(data=PatientOut.model_validate(get_active_patient(db, patient_id)))


@app.post(
    "/patients",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
)
def create_patient(payload: PatientCreate, db: DbSession):
    return Envelope(data=insert_patient(db, payload))


def insert_patient(db: Session, payload: PatientCreate) -> PatientOut:
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    out = PatientOut.model_validate(patient)
    logger.info("Patient created: %s", out.model_dump_json())
    return out


@app.put(
    "/patients/{patient_id}",
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
)
def update_patient(patient_id: UUID, payload: PatientUpdate, db: DbSession):
    patient = get_active_patient(db, patient_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    out = PatientOut.model_validate(patient)
    logger.info("Patient updated: %s", out.model_dump_json())
    return Envelope(data=out)


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


# --- Vapi tool webhook ---

@app.post(
    "/tools/save_patient",
    response_model=VapiToolResponse,
    dependencies=[Depends(require_api_key)],
)
def save_patient_tool(body: VapiToolRequest, db: DbSession):
    """Called by Vapi when the agent invokes `save_patient`, after the caller confirms.

    Validation and database failures still answer 200 in Vapi's `results` format, so
    the agent always gets a result it can act on mid-call. Each `result` is our usual
    envelope as a JSON string: on failure the agent sees `error.message` and
    `error.field` and can re-prompt for just that field.
    """
    results = [
        VapiToolResult(
            name=call.name,
            tool_call_id=call.id,
            result=run_save_patient(db, call).model_dump_json(),
        )
        for call in body.message.tool_call_list
    ]
    return VapiToolResponse(results=results)


def run_save_patient(db: Session, call: VapiToolCall) -> Envelope[PatientOut]:
    if call.name != "save_patient":
        return Envelope(error=ErrorDetail(message=f"Unknown tool: {call.name}"))
    if call.arguments is None:
        return Envelope(error=ErrorDetail(message="Tool call arguments are not valid JSON"))
    try:
        payload = PatientCreate.model_validate(call.arguments)
    except ValidationError as exc:
        _, message, field = describe_validation_error(exc.errors())
        logger.info("save_patient %s rejected: %s (field=%s)", call.id, message, field)
        return Envelope(error=ErrorDetail(message=message, field=field))
    try:
        return Envelope(data=insert_patient(db, payload))
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error in save_patient %s", call.id)
        return Envelope(error=ErrorDetail(message=DATABASE_ERROR_MESSAGE))
