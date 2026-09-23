"""Speech parsing: pure functions, no database."""

from datetime import date

import pytest

from models import Sex
from normalise import (
    collapse_spelled_name,
    parse_date,
    parse_sex,
    spoken_digits,
    spoken_email,
    state_code,
)


@pytest.mark.parametrize("spoken, digits", [
    ("five five five one two three four five six seven", "5551234567"),
    ("(555) 123-4567", "5551234567"),
    ("+1 555 123 4567", "15551234567"),
    ("five five five, oh one two, double three four five", "5550123345"),
    ("eight hundred five five five one two one two", "8005551212"),
    ("five five five twelve thirty four", "5551234"),
    ("my number is 555 123 4567", "5551234567"),
    ("nine oh two one oh", "90210"),
    # Spanish
    ("cinco cinco cinco uno dos tres cuatro cinco seis siete", "5551234567"),
    ("cincuenta y cinco", "55"),
    ("quinientos cincuenta y cinco, trescientos doce, cuarenta y cinco sesenta y siete", "5553124567"),
    ("dieciséis", "16"),
])
def test_spoken_digits(spoken, digits):
    assert spoken_digits(spoken) == digits


@pytest.mark.parametrize("spoken, expected", [
    ("1990-03-15", date(1990, 3, 15)),
    ("03/15/1990", date(1990, 3, 15)),
    ("3/15/05", date(2005, 3, 15)),
    ("March 15th, 1990", date(1990, 3, 15)),
    ("15 March 1990", date(1990, 3, 15)),
    ("March fifteenth nineteen ninety", date(1990, 3, 15)),
    ("the fifteenth of March nineteen ninety", date(1990, 3, 15)),
    ("March twenty first nineteen eighty five", date(1985, 3, 21)),
    ("July fourth two thousand and five", date(2005, 7, 4)),
    ("July fourth nineteen oh five", date(1905, 7, 4)),
    ("January first twenty twenty one", date(2021, 1, 1)),
    # Spanish
    ("15 de marzo de 1990", date(1990, 3, 15)),
    ("quince de marzo de mil novecientos noventa", date(1990, 3, 15)),
    ("el treinta y uno de diciembre de mil novecientos noventa y cinco", date(1995, 12, 31)),
    ("el primero de enero de dos mil cinco", date(2005, 1, 1)),
    ("veintiuno de septiembre de dos mil veinte", date(2020, 9, 21)),
])
def test_parse_date(spoken, expected):
    assert parse_date(spoken) == expected


@pytest.mark.parametrize("spoken", [
    "February 30 1990",       # not a real date
    "March 1990",             # no day: never filled in from today
    "March 15",               # no year
    "15/03/1990",             # day-first is rejected, not swapped
    "July fourth twenty five",  # 1925 or 2025? asked again rather than guessed
    "banana",
])
def test_parse_date_rejects(spoken):
    assert parse_date(spoken) is None


@pytest.mark.parametrize("spoken, sex", [
    ("male", Sex.MALE), ("F", Sex.FEMALE), ("Woman", Sex.FEMALE), ("non-binary", Sex.OTHER),
    ("I'd rather not say", Sex.DECLINE), ("pass", Sex.DECLINE),
    ("masculino", Sex.MALE), ("mujer", Sex.FEMALE), ("varón", Sex.MALE),
    ("prefiero no decir", Sex.DECLINE), ("otro", Sex.OTHER),
    ("banana", None),
])
def test_parse_sex(spoken, sex):
    assert parse_sex(spoken) == sex


@pytest.mark.parametrize("spoken, code", [
    ("CA", "CA"), ("C A", "CA"), ("California", "CA"), ("N.Y.", "NY"),
    ("Washington D.C.", "DC"), ("Puerto Rico", "PR"),
    ("Nueva York", "NY"), ("Carolina del Norte", "NC"), ("Hawái", "HI"),
    ("XX", None),
])
def test_state_code(spoken, code):
    assert state_code(spoken) == code


@pytest.mark.parametrize("spoken, name", [
    ("D A V I S", "Davis"),
    ("D-A-V-I-S", "Davis"),
    ("D. A. V. I. S.", "Davis"),
    ("O apostrophe B R I E N", "O'Brien"),
    ("S M I T H hyphen J O N E S", "Smith-Jones"),
    ("G A R C I A guion L O P E Z", "Garcia-Lopez"),
    ("Anne-Marie", "Anne-Marie"),
    ("Mary J Blige", "Mary J Blige"),
])
def test_collapse_spelled_name(spoken, name):
    assert collapse_spelled_name(spoken) == name


@pytest.mark.parametrize("spoken, email", [
    ("john dot smith at gmail dot com", "john.smith@gmail.com"),
    ("j o h n at gmail dot com", "john@gmail.com"),
    ("mary underscore lee at example dot org", "mary_lee@example.org"),
    ("juan punto perez arroba gmail punto com", "juan.perez@gmail.com"),
    ("ana guion bajo ruiz arroba example punto com", "ana_ruiz@example.com"),
    ("a.b@c.com", "a.b@c.com"),
])
def test_spoken_email(spoken, email):
    assert spoken_email(spoken) == email
