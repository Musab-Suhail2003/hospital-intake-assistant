"""Request/response models. All field validation lives here, not in route handlers.

Validators raise ValueError with a message the voice agent can read back to the caller;
the 422 handler in main.py forwards that message along with the field name.
"""

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Annotated, Generic, Optional, TypeVar
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from models import Sex

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "AS", "GU", "MP", "PR", "VI",
}

# Unicode letters (so "José", "Müller" pass), joined by single spaces, hyphens or apostrophes.
_NAME_RE = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")
_PHONE_FORMATTING_RE = re.compile(r"[\s().\-]")
_ZIP_RE = re.compile(r"\d{5}(?:-\d{4})?")
_MEMBER_ID_RE = re.compile(r"[A-Za-z0-9]+")
_EARLIEST_DOB = date(1900, 1, 1)


def _label(info: ValidationInfo) -> str:
    return info.field_name.replace("_", " ").capitalize()


def _clean_name(value: object, info: ValidationInfo) -> object:
    if not isinstance(value, str):
        return value
    name = unicodedata.normalize("NFC", value).replace("’", "'")
    name = " ".join(name.split())
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
    digits = _PHONE_FORMATTING_RE.sub("", value)
    if digits.startswith("+1"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if not (len(digits) == 10 and digits.isascii() and digits.isdigit()):
        raise ValueError(f"{_label(info)} must be a 10-digit US phone number")
    return digits


def _parse_date_of_birth(value: object) -> date:
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(value.strip(), fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("Date of birth must be a valid date, like 03/15/1990")
    else:
        raise ValueError("Date of birth must be a valid date, like 03/15/1990")

    if parsed > datetime.now(timezone.utc).date():
        raise ValueError("Date of birth cannot be in the future")
    if parsed < _EARLIEST_DOB:
        raise ValueError("Date of birth must be after 1900")
    return parsed


def _parse_sex(value: object) -> Sex:
    if isinstance(value, str):
        for member in Sex:
            if value.strip().lower() == member.value.lower():
                return member
    raise ValueError("Sex must be Male, Female, Other, or Decline to Answer")


def _normalise_state(value: object) -> object:
    if not isinstance(value, str):
        return value
    code = value.strip().upper()
    if code not in US_STATES:
        raise ValueError("State must be a valid 2-letter US state abbreviation")
    return code


def _check_zip(value: object) -> object:
    if not isinstance(value, str):
        return value
    zip_code = value.strip()
    if not _ZIP_RE.fullmatch(zip_code):
        raise ValueError("ZIP code must be 5 digits or ZIP+4, like 12345 or 12345-6789")
    return zip_code


def _check_email(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        raise ValueError("Email must be a valid email address, like name@example.com")


def _check_member_id(value: object) -> object:
    if not isinstance(value, str):
        return value
    member_id = value.strip()
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
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ErrorDetail(BaseModel):
    message: str
    field: str | None = None


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
