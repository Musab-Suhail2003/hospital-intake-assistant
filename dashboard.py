"""Server-rendered HTML for GET /dashboard: one page, one table, no templates or JavaScript.

Every value from the database goes through html.escape, because free-text fields such as
the address and reason for visit come from callers.
"""

from datetime import timezone
from html import escape

from uuid import UUID

from schemas import CallOut, PatientOut

REFRESH_SECONDS = 30

_COLUMNS = (
    "Patient", "Date of birth", "Sex", "Phone", "Email", "Address", "Reason for visit",
    "Insurance", "Emergency contact", "Language", "Next appointment", "Registered (UTC)",
)
_CALL_COLUMNS = ("Ended (UTC)", "Caller", "Patient", "Outcome", "Summary", "Transcript")

_STYLE = """
:root { color-scheme: light dark; --bg: #f7f7f5; --fg: #1d1d1b; --muted: #6b6b66;
  --line: #e3e2dd; --head: #efeee9; --accent: #2f5d8a; --err-bg: #fdecea; --err-fg: #8a1c12; }
@media (prefers-color-scheme: dark) { :root { --bg: #151514; --fg: #ecebe6; --muted: #9a9993;
  --line: #2c2c2a; --head: #1f1f1d; --accent: #8db4dc; --err-bg: #3a1714; --err-fg: #f4b6ae; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, form, .notice, .table-wrap { max-width: 1400px; margin: 0 auto 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: var(--muted); margin: 0; }
a { color: var(--accent); }
form { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: end; }
label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--muted); }
input { font: inherit; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px;
  background: transparent; color: var(--fg); min-width: 160px; }
button { font: inherit; padding: 6px 14px; border: 0; border-radius: 6px;
  background: var(--accent); color: var(--bg); cursor: pointer; }
.notice { padding: 10px 12px; border-radius: 6px; background: var(--err-bg); color: var(--err-fg); }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--head); font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
td:empty::after { content: "\\2014"; color: var(--muted); }
.nowrap { white-space: nowrap; }
.wide { min-width: 170px; }
.empty { padding: 32px; text-align: center; color: var(--muted); }
h2 { font-size: 17px; max-width: 1400px; margin: 32px auto 8px; }
details summary { cursor: pointer; color: var(--accent); }
pre { white-space: pre-wrap; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  margin: 8px 0 0; max-height: 320px; overflow-y: auto; }
"""


def _text(value: object) -> str:
    return "" if value is None else escape(str(value))


def _phone(digits: str | None) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if digits else ""


def _joined(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _row(p: PatientOut, appointment: str | None) -> str:
    address = ", ".join(part for part in (p.address_line_1, p.address_line_2, p.city) if part)
    member_id = f"ID {p.insurance_member_id}" if p.insurance_member_id else None
    cells = (
        f'<td class="nowrap"><strong>{_text(p.first_name)} {_text(p.last_name)}</strong></td>',
        f'<td class="nowrap">{p.date_of_birth.strftime("%m/%d/%Y")}</td>',
        f"<td>{_text(p.sex.value)}</td>",
        f'<td class="nowrap">{_phone(p.phone_number)}</td>',
        f"<td>{_text(p.email)}</td>",
        f'<td class="wide">{_text(f"{address}, {p.state} {p.zip_code}")}</td>',
        f'<td class="wide">{_text(p.reason_for_visit)}</td>',
        f"<td>{_text(_joined(p.insurance_provider, member_id))}</td>",
        f"<td>{_text(_joined(p.emergency_contact_name, _phone(p.emergency_contact_phone)))}</td>",
        f"<td>{_text(p.preferred_language)}</td>",
        f'<td class="nowrap">{_text(appointment)}</td>',
        f'<td class="nowrap">{p.created_at.astimezone(timezone.utc):%Y-%m-%d %H:%M}</td>',
    )
    return "<tr>" + "".join(cells) + "</tr>"


def _filter_input(label: str, name: str, filters: dict[str, str], placeholder: str) -> str:
    value = _text(filters.get(name, ""))
    return (
        f'<label>{label}<input name="{name}" value="{value}" placeholder="{placeholder}"></label>'
    )


def _call_row(c: CallOut) -> str:
    ended = c.ended_at or c.started_at
    transcript = (
        f"<details><summary>Show</summary><pre>{_text(c.transcript)}</pre></details>"
        if c.transcript else ""
    )
    cells = (
        f'<td class="nowrap">{f"{ended.astimezone(timezone.utc):%Y-%m-%d %H:%M}" if ended else ""}</td>',
        f'<td class="nowrap">{_text(c.caller_number)}</td>',
        f'<td class="nowrap">{_text(c.patient_name)}</td>',
        f"<td>{_text(c.ended_reason.replace('-', ' ') if c.ended_reason else None)}</td>",
        f'<td class="wide">{_text(c.summary)}</td>',
        f'<td class="wide">{transcript}</td>',
    )
    return "<tr>" + "".join(cells) + "</tr>"


def _calls_section(calls: list[CallOut]) -> str:
    if calls:
        rows = "".join(_call_row(c) for c in calls)
    else:
        rows = f'<tr><td class="empty" colspan="{len(_CALL_COLUMNS)}">No calls recorded yet.</td></tr>'
    headings = "".join(f"<th>{column}</th>" for column in _CALL_COLUMNS)
    return f"""<h2>Recent calls</h2>
<div class="table-wrap">
<table>
<thead><tr>{headings}</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""


def render_dashboard(
    patients: list[PatientOut],
    filters: dict[str, str],
    error: str | None = None,
    appointments: dict[UUID, str] | None = None,
    calls: list[CallOut] | None = None,
) -> str:
    """The full page. `filters` holds the raw query values, echoed back into the form.

    `appointments` maps a patient to their next appointment, already worded for display.
    """
    appointments = appointments or {}
    if patients:
        rows = "".join(_row(p, appointments.get(p.patient_id)) for p in patients)
    else:
        message = "No patients match these filters." if filters else "No patients registered yet."
        rows = f'<tr><td class="empty" colspan="{len(_COLUMNS)}">{message}</td></tr>'
    noun = "patient" if len(patients) == 1 else "patients"
    count = f"{len(patients)} {'matching ' if filters else ''}{noun}"
    notice = f'<p class="notice">{_text(error)}</p>' if error else ""
    headings = "".join(f"<th>{column}</th>" for column in _COLUMNS)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<title>Patient Registrations</title>
<style>{_STYLE}</style>
</head>
<body>
<header style="text-align: center;">
<h1>Registered patients</h1>
<p class="meta"> {count} · <a href="/docs">API docs</a></p>
</header>
<form style="text-align: center;" method="get" action="/dashboard">
{_filter_input("Last name", "last_name", filters, "Davis")}
{_filter_input("Date of birth", "date_of_birth", filters, "MM/DD/YYYY")}
{_filter_input("Phone", "phone_number", filters, "555 123 4567")}
<button type="submit">Filter</button>
<a href="/dashboard">Clear</a>
</form>
{notice}
<div class="table-wrap">
<table>
<thead><tr>{headings}</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
{_calls_section(calls or [])}
</body>
</html>
"""
