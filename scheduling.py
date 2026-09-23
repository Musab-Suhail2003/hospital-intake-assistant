"""Mock appointment slots for the first-appointment booking.

One provider, weekdays 9:00 to 17:00 in the practice's timezone, 30-minute visits, bookable
from tomorrow up to two weeks ahead. Times are stored in UTC and spoken in practice time.
Functions take `now` so tests can pin the clock.
"""

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PRACTICE_TZ = ZoneInfo(os.environ.get("PRACTICE_TIMEZONE", "America/New_York"))
OPENS, CLOSES = time(9, 0), time(17, 0)
SLOT_MINUTES = 30
DAYS_AHEAD = 14
NOON = time(12, 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bookable_days(now: datetime | None = None) -> list[date]:
    """Weekdays from tomorrow (practice time) up to DAYS_AHEAD days out."""
    today = (now or _now()).astimezone(PRACTICE_TZ).date()
    days = (today + timedelta(days=n) for n in range(1, DAYS_AHEAD + 1))
    return [day for day in days if day.weekday() < 5]


def slots_on(day: date) -> list[datetime]:
    """Every slot start on a weekday, as UTC datetimes. Weekends have none."""
    if day.weekday() >= 5:
        return []
    first = datetime.combine(day, OPENS, tzinfo=PRACTICE_TZ)
    count = (CLOSES.hour * 60 + CLOSES.minute - OPENS.hour * 60 - OPENS.minute) // SLOT_MINUTES
    return [(first + timedelta(minutes=SLOT_MINUTES * i)).astimezone(timezone.utc) for i in range(count)]


def is_bookable(starts_at: datetime, now: datetime | None = None) -> bool:
    """True if starts_at is exactly one of the slots in the bookable window."""
    day = starts_at.astimezone(PRACTICE_TZ).date()
    return day in bookable_days(now) and starts_at.astimezone(timezone.utc) in slots_on(day)


def open_slots(
    booked: set[datetime],
    day: date | None = None,
    part_of_day: str | None = None,
    limit: int = 6,
    now: datetime | None = None,
) -> list[datetime]:
    """The earliest free slots, optionally on one day and in the morning or afternoon."""
    days = [day] if day else bookable_days(now)
    free = []
    for d in days:
        if d not in bookable_days(now):
            continue
        for slot in slots_on(d):
            local = slot.astimezone(PRACTICE_TZ).time()
            if part_of_day == "morning" and local >= NOON:
                continue
            if part_of_day == "afternoon" and local < NOON:
                continue
            if slot not in booked:
                free.append(slot)
    return free[:limit]


def spoken(starts_at: datetime) -> str:
    """How the agent reads a slot out: "Tuesday, September 29 at 9:30 AM"."""
    local = starts_at.astimezone(PRACTICE_TZ)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%A, %B} {local.day} at {hour}:{local:%M %p}"
