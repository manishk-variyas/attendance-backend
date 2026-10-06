# Analytics rules

Endpoints under `/api/analytics/*`. Full API reference: `docs/analytics-api.md`.
Attendance algorithm walkthrough: `docs/attendance-analytics-plan.md`.

## Working days = the roster (shifts)
A day is a working day for a member **only if they have a `shifts` row** that
day — regardless of weekday/weekend/holiday. No shift ⇒ not a working day.
This applies to attendance, absenteeism, and leaves/WFH `approved_days`.

## Scope
Team endpoints include **active employees only**:
`redmine.users.status = 1 AND employee_master.is_active = true` (with a default
email). Inactive/test/service accounts are hidden.

## Attendance (`attendance/me|member|team`)
- `present_days` = `status = present` (office or WFH)
- `wfh_days` = present with `work_location_status = WFH`; `office_days = present − wfh`
- `leave_days` = leave status / `work_location_status = LEAVE`
- `absent_days = max(working_days − present_days − leave_days, 0)`
- `attendance_pct = present_days / working_days × 100`
- `holiday_days` = holidays in range (all holidays, deduped by date)

## Punctuality
Late = `is_late` on attendance rows. Avg/earliest/latest check-in in IST.

## Absenteeism
Streaks run over the member's **working days**; non-working days don't break a
run. Red flag = streak ≥ `streak_min` (default 3).

## Tickets
- `scope=current` (default): header counts = all my tickets now.
- `scope=range`: header = tickets **created in range** with current statuses.
- Status buckets: `closed` = `is_closed`; `pending` = `Resolved`; `open` = other.
- `overdue` uses IST today.

## Leaves / WFH
Requests **overlapping** the range. `approved_days` = overlap with the member's
shift days. `monthly` series spans the requested window.

## Conventions
- IST is the day boundary everywhere.
- Max range 366 days.
- Daily boundaries for tickets pinned with `(now() AT TIME ZONE 'Asia/Kolkata')::date`.
