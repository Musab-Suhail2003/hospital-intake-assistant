"""Turn speech-shaped input into canonical values.

The voice agent may pass a field through much as the caller said it: "five five five
one two three ...", "March fifteenth nineteen ninety", "D A V I S", "California".
English and Spanish are understood ("cinco cinco cinco", "quince de marzo de mil
novecientos noventa", "Nueva York"); accents are ignored when matching words.
Each function returns the canonical value, or None when it can't make sense of the
input. The validators in schemas.py decide what is acceptable and word the error the
agent reads back to the caller.
"""

import re
import unicodedata
from datetime import date, datetime, timezone

from models import Sex

_TOKEN_RE = re.compile(r"[a-z]+|[0-9]+")

# Word tables hold English and Spanish side by side; Spanish is written without accents
# because _tokens() strips them.
_UNITS = {
    "zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
}
# Single words for 10-29 that aren't a tens word plus a unit.
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veintiuno": 21, "veintiun": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
}
_SPANISH_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
    "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600, "setecientos": 700,
    "ochocientos": 800, "novecientos": 900,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30,
    "primero": 1, "primer": 1,  # Spanish uses cardinals for dates, except the first
}
_ZERO_WORDS = ("zero", "oh", "o", "cero")


def _plain(text: str) -> str:
    """Lower-case with accents removed, so "Dieciséis" matches "dieciseis"."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_plain(text))


def _word_value(token: str) -> int:
    if token.isdigit():
        return int(token)
    for table in (_UNITS, _TEENS, _TENS, _ORDINALS):
        if token in table:
            return table[token]
    raise ValueError(f"not a number: {token}")


def _capitalise(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


# --- Digits: phone numbers, ZIP codes ---

def spoken_digits(text: str) -> str:
    """Extract a digit string from formatted or spoken input.

    "(555) 123-4567" and "five five five one two three four five six seven" both give
    "5551234567". Also handles "oh", "double five", "twelve", "thirty four" and
    "eight hundred", and Spanish groups such as "cincuenta y cinco" and "quinientos
    cincuenta y cinco". Non-number words ("plus", "dash", "my number is") are dropped;
    the caller checks the resulting length.
    """
    tokens = _tokens(text)
    digits: list[str] = []
    repeat = 1
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token in ("double", "triple"):
            repeat = 2 if token == "double" else 3
            continue
        if token.isdigit():
            part = token
        elif token in _UNITS:
            part = str(_UNITS[token])
        elif token in _TEENS:
            part = str(_TEENS[token])
        elif token in _TENS:
            value, i = _tens_group(tokens, i, _TENS[token])
            part = str(value)
        elif token in _SPANISH_HUNDREDS:
            # "quinientos cincuenta y cinco" is 555; "trescientos doce" is 312.
            value = _SPANISH_HUNDREDS[token]
            if i < len(tokens) and tokens[i] in _TENS:
                tens, i = _tens_group(tokens, i + 1, _TENS[tokens[i]])
                value += tens
            elif i < len(tokens) and (tokens[i] in _TEENS or _UNITS.get(tokens[i], 0) > 0):
                value += _word_value(tokens[i])
                i += 1
            part = str(value)
        elif token == "hundred":
            part = "00"
        elif token == "thousand":
            part = "000"
        else:
            continue
        digits.append(part * repeat)
        repeat = 1
    return "".join(digits)


def _tens_group(tokens: list[str], i: int, tens: int) -> tuple[int, int]:
    """A tens word already read, plus an optional unit: "thirty four", "cincuenta y cinco".

    Returns the value and the index after the group. A bare "thirty" is 30.
    """
    j = i + 1 if i < len(tokens) and tokens[i] == "y" else i
    if j < len(tokens) and _UNITS.get(tokens[j], 0) > 0:
        return tens + _UNITS[tokens[j]], j + 1
    return tens, i


# --- Dates ---

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
# Dropped before parsing: "the fifteenth of March", "March 15th", "two thousand and five",
# "el quince de marzo de mil novecientos noventa y cinco".
_DATE_FILLER = {"the", "of", "and", "st", "nd", "rd", "th", "el", "de", "del", "y"}
_CENTURY_WORDS = ("eighteen", "nineteen", "twenty")


def _is_compound(tokens: list[str]) -> bool:
    """True for "twenty one" / "thirty first": a tens word then a 1-9 unit or ordinal."""
    return (
        len(tokens) == 2
        and tokens[0] in _TENS
        and 1 <= _UNITS.get(tokens[1], _ORDINALS.get(tokens[1], 0)) <= 9
    )


def _small_number(tokens: list[str]) -> int:
    """A number from one or two tokens: "15", "fifteenth", "twenty first", "oh five"."""
    if len(tokens) == 1:
        return _word_value(tokens[0])
    if len(tokens) == 2 and (_is_compound(tokens) or tokens[0] in _ZERO_WORDS):
        return _word_value(tokens[0]) + _word_value(tokens[1])
    raise ValueError(f"not a number: {tokens}")


def _year(tokens: list[str]) -> int:
    """"1990", "90", "nineteen ninety", "nineteen oh five", "two thousand five"."""
    if len(tokens) == 1 and tokens[0].isdigit():
        if len(tokens[0]) == 4:
            return int(tokens[0])
        if len(tokens[0]) == 2:
            # A birth year can't be in the future, so "05" is 2005 but "90" is 1990.
            year = 2000 + int(tokens[0])
            return year if year <= datetime.now(timezone.utc).year else year - 100
        raise ValueError(f"not a year: {tokens}")

    if "mil" in tokens or any(t in _SPANISH_HUNDREDS for t in tokens):
        return _spanish_number(tokens)  # "mil novecientos noventa", "dos mil cinco"

    for word, scale in (("thousand", 1000), ("hundred", 100)):
        if word in tokens:  # "two thousand five", "nineteen hundred"
            at = tokens.index(word)
            tail = tokens[at + 1:]
            return _small_number(tokens[:at]) * scale + (_small_number(tail) if tail else 0)

    # "nineteen ninety five": a century word, then the last two digits. The remainder
    # must be 10+ or start with "oh"; "twenty five" is ambiguous, so it is rejected.
    if len(tokens) >= 2 and tokens[0] in _CENTURY_WORDS:
        rest = _small_number(tokens[1:])
        if 10 <= rest < 100 or tokens[1] in _ZERO_WORDS:
            return _word_value(tokens[0]) * 100 + rest
    raise ValueError(f"not a year: {tokens}")


def _spanish_number(tokens: list[str]) -> int:
    """Spanish numbers are additive: mil novecientos noventa cinco = 1000 + 900 + 90 + 5.

    "mil" multiplies what came before it, so "dos mil cinco" is 2005.
    """
    total = current = 0
    for token in tokens:
        if token == "mil":
            total += (current or 1) * 1000
            current = 0
        elif token in _SPANISH_HUNDREDS:
            current += _SPANISH_HUNDREDS[token]
        else:
            current += _word_value(token)
    return total + current


def parse_date(text: str) -> date | None:
    """Parse "1990-03-15", "03/15/1990", "March 15th, 1990", "15 March 1990" or
    "March fifteenth nineteen ninety", or Spanish such as "15 de marzo de 1990" and
    "quince de marzo de mil novecientos noventa". All-numeric dates are read month-first
    (US).

    Returns None unless the text is a real calendar date.
    """
    tokens = [t for t in _tokens(text) if t not in _DATE_FILLER]
    month_at = next((i for i, t in enumerate(tokens) if t in _MONTHS), None)
    try:
        if month_at is None:
            if len(tokens) != 3 or not all(t.isdigit() for t in tokens):
                return None
            if len(tokens[0]) == 4:
                year, month, day = (int(t) for t in tokens)
            else:
                month, day, year = int(tokens[0]), int(tokens[1]), _year(tokens[2:])
        else:
            month = _MONTHS[tokens[month_at]]
            before, after = tokens[:month_at], tokens[month_at + 1:]
            if before:  # "15 March 1990", "the fifteenth of March nineteen ninety"
                day, year = _small_number(before), _year(after)
            else:  # "March 15 1990", "March twenty first nineteen ninety"
                split = 2 if _is_compound(after[:2]) else 1
                day, year = _small_number(after[:split]), _year(after[split:])
        return date(year, month, day)
    except ValueError:
        return None


# --- Sex ---

_SEX_WORDS = {
    "male": Sex.MALE, "m": Sex.MALE, "man": Sex.MALE,
    "female": Sex.FEMALE, "f": Sex.FEMALE, "woman": Sex.FEMALE,
    "other": Sex.OTHER, "intersex": Sex.OTHER,
    "non-binary": Sex.OTHER, "nonbinary": Sex.OTHER, "non binary": Sex.OTHER,
    "masculino": Sex.MALE, "hombre": Sex.MALE, "varon": Sex.MALE,
    "femenino": Sex.FEMALE, "mujer": Sex.FEMALE,
    "otro": Sex.OTHER, "otra": Sex.OTHER, "no binario": Sex.OTHER, "no binaria": Sex.OTHER,
    "intersexual": Sex.OTHER,
}
# Anything that sounds like a refusal: "I'd rather not say", "prefer not to", "pass".
_DECLINE_RE = re.compile(
    r"\b(?:decline|rather not|prefer not|not (?:to )?(?:say|answer|share)"
    r"|don'?t want|do not want|no answer|no comment|skip|pass|private|personal"
    r"|prefiero no|no quiero|no deseo|paso|privado)\b"
)


def parse_sex(text: str) -> Sex | None:
    key = " ".join(_plain(text.replace("’", "'")).strip(" .!?,").split())
    if key in _SEX_WORDS:
        return _SEX_WORDS[key]
    if _DECLINE_RE.search(key):
        return Sex.DECLINE
    return None


# --- US states and territories ---

_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "american samoa": "AS",
    "guam": "GU", "northern mariana islands": "MP", "puerto rico": "PR",
    "virgin islands": "VI", "us virgin islands": "VI",
    # Spanish names that differ from the English ones
    "nueva york": "NY", "nuevo mexico": "NM", "nueva jersey": "NJ", "nuevo hampshire": "NH",
    "carolina del norte": "NC", "carolina del sur": "SC", "dakota del norte": "ND",
    "dakota del sur": "SD", "virginia occidental": "WV", "pensilvania": "PA",
    "luisiana": "LA", "misuri": "MO", "misisipi": "MS", "hawai": "HI",
    "distrito de columbia": "DC", "samoa americana": "AS",
    "islas marianas del norte": "MP", "islas virgenes": "VI",
}
US_STATE_CODES = frozenset(_STATE_NAMES.values())


def state_code(text: str) -> str | None:
    """"CA", "ca", "C A", "California", "Nueva York", "Washington D.C." -> the 2-letter code."""
    key = " ".join(re.sub(r"[.'’]", "", _plain(text)).split())
    compact = key.replace(" ", "").upper()
    if compact in US_STATE_CODES:
        return compact
    return _STATE_NAMES.get(key)


# --- Names ---

# Two or more single letters separated by spaces, hyphens or periods: "D A V I S",
# "D-A-V-I-S", "D. A. V. I. S.". A spaced hyphen (" - ") is not a separator, so
# "S M I T H - J O N E S" keeps its hyphen.
_SPELLED_RE = re.compile(r"\b[^\W\d_](?:(?:\s+|-|\.\s*)[^\W\d_]\b)+\.?")
_SPOKEN_NAME_PUNCTUATION_RE = re.compile(r"\b(hyphen|gui[oó]n|apostrophe)\b", re.IGNORECASE)


def collapse_spelled_name(text: str) -> str:
    """"D A V I S" -> "Davis"; "O apostrophe B R I E N" -> "O'Brien".

    Names that aren't spelled out pass through unchanged apart from spacing around
    hyphens and apostrophes ("Smith - Jones" -> "Smith-Jones").
    """
    text = _SPOKEN_NAME_PUNCTUATION_RE.sub(
        lambda m: " ' " if m[1].lower() == "apostrophe" else " - ", text
    )
    text = _SPELLED_RE.sub(lambda m: _capitalise("".join(filter(str.isalpha, m[0]))), text)
    return re.sub(r"\s*([-'])\s*", r"\1", text)


# --- Email ---

_SPOKEN_EMAIL = (
    (re.compile(r"\s+(?:at|arroba)\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s+(?:dot|punto)\s+", re.IGNORECASE), "."),
    (re.compile(r"\s*\b(?:underscore|gui[oó]n bajo)\b\s*", re.IGNORECASE), "_"),
    (re.compile(r"\s*\b(?:dash|hyphen|gui[oó]n)\b\s*", re.IGNORECASE), "-"),
)


def spoken_email(text: str) -> str:
    """"john dot smith at gmail dot com" or "juan punto perez arroba gmail punto com"
    -> "john.smith@gmail.com" / "juan.perez@gmail.com".

    Text that already contains an @ is returned unchanged.
    """
    if "@" in text:
        return text
    for pattern, symbol in _SPOKEN_EMAIL:
        text = pattern.sub(symbol, text)
    return "".join(text.split())
