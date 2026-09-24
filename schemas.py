"""Request/response models. All field validation lives here, not in route handlers.

Validators raise ValueError with a message the voice agent can read back to the caller;
the 422 handler in main.py forwards that message along with the field name.
"""

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Annotated, Any, Generic, Literal, Optional, TypeVar
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from models import Sex
from normalise import (
    collapse_spelled_name,
    parse_date,
    parse_sex,
    spoken_digits,
    spoken_email,
    state_code,
)

# Unicode letters (so "José", "Müller" pass), joined by single spaces, hyphens or apostrophes.
_NAME_RE = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")
_MEMBER_ID_RE = re.compile(r"[A-Z0-9]+")
_EARLIEST_DOB = date(1900, 1, 1)


def _label(info: ValidationInfo) -> str:
    return info.field_name.replace("_", " ").capitalize()


def _clean_name(value: object, info: ValidationInfo) -> object:
    if not isinstance(value, str):
        return value
    name = unicodedata.normalize("NFC", value).replace("’", "'")
    name = " ".join(collapse_spelled_name(name).split())
    if not name:
        raise ValueError(f"{_label(info)} is required")
    if len(name) > 50:
        raise ValueError(f"{_label(info)} must be 50 characters or fewer")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"{_label(info)} may only contain letters, spaces, hyphens and apostrophes"
        )
    return name


def _normalise_phone(value: object, info: ValidationInfo) -> object:
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return value
    digits = spoken_digits(value)
    if len(digits) == 11 and digits.startswith("1"):  # "+1 555 ...", "1-800-..."
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"{_label(info)} must be a 10-digit US phone number")
    return digits


def _parse_date_of_birth(value: object) -> date:
    if isinstance(value, date):
        parsed = value
    else:
        parsed = parse_date(value) if isinstance(value, str) else None
        if parsed is None:
            raise ValueError("Date of birth must be a valid date, like 03/15/1990")

    if parsed > datetime.now(timezone.utc).date():
        raise ValueError("Date of birth cannot be in the future")
    if parsed < _EARLIEST_DOB:
        raise ValueError("Date of birth must be after 1900")
    return parsed


def _parse_sex(value: object) -> Sex:
    sex = parse_sex(value) if isinstance(value, str) else None
    if sex is None:
        raise ValueError("Sex must be Male, Female, Other, or Decline to Answer")
    return sex


def _normalise_state(value: object) -> object:
    if not isinstance(value, str):
        return value
    code = state_code(value)
    if code is None:
        raise ValueError("State must be a US state name or 2-letter abbreviation, like CA")
    return code


def _check_zip(value: object) -> object:
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return value
    digits = spoken_digits(value)
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    raise ValueError("ZIP code must be 5 digits or ZIP+4, like 12345 or 12345-6789")


def _check_email(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        email = spoken_email(value.strip())
        return validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        raise ValueError("Email must be a valid email address, like name@example.com")


def _check_member_id(value: object) -> object:
    if not isinstance(value, str):
        return value
    # Spoken IDs arrive as "A B C 1 2 3" or "abc-123"; letters from speech have no case.
    member_id = re.sub(r"[\s\-]", "", value).upper()
    if not _MEMBER_ID_RE.fullmatch(member_id):
        raise ValueError("Insurance member ID may only contain letters and numbers")
    return member_id


def _blank_to_none(value: object) -> object:
    # Voice agents often send "" for an optional field the caller skipped.
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _default_language(value: object) -> object:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "English"
    return value


def _text(max_length: int, min_length: int = 0):
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
    ]


def _optional(inner):
    return Annotated[Optional[inner], BeforeValidator(_blank_to_none)]


Name = Annotated[str, BeforeValidator(_clean_name)]
Phone = Annotated[str, BeforeValidator(_normalise_phone)]
DateOfBirth = Annotated[date, BeforeValidator(_parse_date_of_birth)]
SexField = Annotated[Sex, BeforeValidator(_parse_sex)]
State = Annotated[str, BeforeValidator(_normalise_state)]
ZipCode = Annotated[str, BeforeValidator(_check_zip)]
Email = Annotated[str, BeforeValidator(_check_email)]
MemberId = Annotated[str, StringConstraints(max_length=50), BeforeValidator(_check_member_id)]
Language = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=50),
    BeforeValidator(_default_language),
]


class PatientCreate(BaseModel):
    first_name: Name
    last_name: Name
    date_of_birth: DateOfBirth
    sex: SexField
    phone_number: Phone
    email: _optional(Email) = None
    address_line_1: _text(200, min_length=1)
    address_line_2: _optional(_text(200)) = None
    city: _text(100, min_length=1)
    state: State
    zip_code: ZipCode
    reason_for_visit: _optional(_text(200)) = None
    insurance_provider: _optional(_text(100)) = None
    insurance_member_id: _optional(MemberId) = None
    preferred_language: Language = "English"
    emergency_contact_name: _optional(_text(100)) = None
    emergency_contact_phone: _optional(Phone) = None


# Columns that are NOT NULL in the database; a PUT may change them but not clear them.
_REQUIRED_ON_UPDATE = (
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code", "preferred_language",
)


class PatientUpdate(BaseModel):
    """Partial update: only fields present in the request body are applied."""

    first_name: Optional[Name] = None
    last_name: Optional[Name] = None
    date_of_birth: Optional[DateOfBirth] = None
    sex: Optional[SexField] = None
    phone_number: Optional[Phone] = None
    email: _optional(Email) = None
    address_line_1: Optional[_text(200, min_length=1)] = None
    address_line_2: _optional(_text(200)) = None
    city: Optional[_text(100, min_length=1)] = None
    state: Optional[State] = None
    zip_code: Optional[ZipCode] = None
    reason_for_visit: _optional(_text(200)) = None
    insurance_provider: _optional(_text(100)) = None
    insurance_member_id: _optional(MemberId) = None
    preferred_language: Optional[Language] = None
    emergency_contact_name: _optional(_text(100)) = None
    emergency_contact_phone: _optional(Phone) = None

    # Only runs for fields the client actually sent, so omitted fields stay untouched.
    @field_validator(*_REQUIRED_ON_UPDATE)
    @classmethod
    def _reject_null(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            raise ValueError(f"{_label(info)} is required and cannot be removed")
        return value


class PatientFilters(BaseModel):
    """Query parameters for GET /patients, normalised the same way as writes."""

    last_name: Optional[_text(50, min_length=1)] = None
    date_of_birth: Optional[DateOfBirth] = None
    phone_number: Optional[Phone] = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    reason_for_visit: str | None
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class FindPatientArgs(BaseModel):
    phone_number: Phone


class PatientMatch(BaseModel):
    """What find_patient reveals about a match: enough to ask "is this you?", no more."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: UUID
    first_name: str
    last_name: str


class UpdatePatientArgs(PatientUpdate):
    patient_id: UUID


def _parse_appointment_day(value: object) -> object:
    if not isinstance(value, str):
        return value
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError("Day must be a valid date, like 2026-09-29")
    return parsed


def _parse_part_of_day(value: object) -> object:
    if not isinstance(value, str):
        return value
    words = {"morning": "morning", "mañana": "morning", "afternoon": "afternoon", "tarde": "afternoon"}
    part = words.get(value.strip().lower())
    if part is None:
        raise ValueError('Part of day must be "morning" or "afternoon"')
    return part


class SlotQuery(BaseModel):
    """Filters for free appointment slots. Both are optional."""

    day: _optional(Annotated[date, BeforeValidator(_parse_appointment_day)]) = None
    part_of_day: _optional(
        Annotated[Literal["morning", "afternoon"], BeforeValidator(_parse_part_of_day)]
    ) = None


class BookAppointment(BaseModel):
    patient_id: UUID
    starts_at: AwareDatetime  # as returned by the slot search, with its UTC offset


class SlotOut(BaseModel):
    starts_at: datetime
    label: str  # how the agent says it, in the practice's timezone


class AppointmentOut(BaseModel):
    appointment_id: UUID
    patient_id: UUID
    starts_at: datetime
    label: str
    created_at: datetime
    cancelled_at: datetime | None


class CallOut(BaseModel):
    call_id: str
    patient_id: UUID | None
    patient_name: str | None
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    ended_reason: str | None
    summary: str | None
    transcript: str | None


class ErrorDetail(BaseModel):
    message: str
    field: str | None = None


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None


# --- Vapi tool-call webhook. Field names on the wire are camelCase. ---

class VapiToolCall(BaseModel):
    """One entry of Vapi's `message.toolCallList`, flattened to id / name / arguments.

    Vapi's docs show three shapes: `{"function": {"name", "arguments"}}`, flat
    `{"name", "arguments"}` and flat `{"name", "parameters"}`, with arguments either an
    object or a JSON string. All of them are accepted. Arguments that are not valid
    JSON become None, so that one call gets an error result instead of the whole
    request failing.
    """

    id: str
    name: str
    arguments: dict[str, Any] | None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        function = data.get("function") or {}
        arguments = function.get("arguments", data.get("arguments", data.get("parameters")))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = None
        elif arguments is None:
            arguments = {}
        return {
            "id": data.get("id"),
            "name": function.get("name", data.get("name")),
            "arguments": arguments,
        }


class VapiCustomer(BaseModel):
    number: str | None = None


class VapiCall(BaseModel):
    """The phone call a message belongs to. Only the fields this API uses."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    customer: VapiCustomer | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class VapiMessage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    call: VapiCall | None = None  # links a save to the phone call it happened in
    # Absent on non-tool messages (status updates etc.), which get an empty reply.
    tool_call_list: list[VapiToolCall] = []


class VapiToolRequest(BaseModel):
    message: VapiMessage


class VapiToolResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str
    tool_call_id: str
    result: str


class VapiToolResponse(BaseModel):
    results: list[VapiToolResult]


class VapiArtifact(BaseModel):
    transcript: str | None = None


class VapiAnalysis(BaseModel):
    summary: str | None = None


class VapiEventMessage(BaseModel):
    """An assistant-level server message. Only end-of-call reports are stored.

    Transcript and summary are read from `artifact` and `analysis`, falling back to the
    top-level fields older payloads used.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    type: str
    call: VapiCall | None = None
    ended_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    artifact: VapiArtifact | None = None
    analysis: VapiAnalysis | None = None
    transcript: str | None = None
    summary: str | None = None


class VapiEvent(BaseModel):
    message: VapiEventMessage
