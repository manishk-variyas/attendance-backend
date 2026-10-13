# Shift-Level Timing Policy

Attendance timing policy (auto-checkout, grace, reminders, check-in limits,
half-day) is defined **per shift** in `shift_master`. It is **not** company-wide
and not hardcoded. A shift is the single source of truth.

## Why
Previously these rules were either global (`system_settings`) or hardcoded, which
meant one cutoff/grace had to fit every shift (day, night, multi-timezone). A
single wall-clock cutoff can't express per-shift policy, and the auto-checkout
logic was duplicated in two places (`/today` and the background sweeper).

## Columns added to `shift_master`
| Column | Type | Default | Meaning |
|---|---|---|---|
| `auto_checkout_enabled` | bool | `true` | Turn auto-checkout on/off for this shift |
| `auto_checkout_grace_minutes` | int | `60` | Auto-checkout fires at **shift_end + grace** |
| `grace_minutes` | int | `15` | Late/early tolerance for check-in & check-out |
| `checkin_reminder_delay_minutes` | int | `30` | Missed check-in reminder delay after shift start |
| `checkout_reminder_grace_hours` | int | `2` | Check-out reminder grace after shift end |
| `early_checkin_limit_minutes` | int | `60` | Earliest accepted check-in before shift start |
| `checkin_cutoff_minutes` | int | `120` | Latest accepted check-in after shift start (shift "ended") |
| `half_day_minutes` | int | `240` | Below this many worked minutes → `half_day` flag |

All `NOT NULL` with defaults — no fallback logic.

### Auto-checkout rule
```
if not shift.auto_checkout_enabled: skip
auto_checkout_dt = shift_end + timedelta(minutes=shift.auto_checkout_grace_minutes)
```
`shift_end` already handles overnight rollover and the shift's own timezone, so
day/night/multi-timezone shifts all behave correctly (no more `max(22:00, shift_end)`).

## Resolver
`app/utils/shift_policy.py` → `resolve_shift_policy(sd) -> ShiftPolicy`. Every
call site reads this; no policy logic is duplicated. Defaults in
`app/constants.py` are used only when a column is missing/None (e.g. a detached
object).

## Enforced at
- `features/attendance/routes.py` — check-in `is_late` + early limit; `_compute_attendance_flags` (grace, half-day); `/today` lazy auto-checkout.
- `features/attendance/services/shift_attendance_service.py` — per-shift check-in `is_late`, early limit, check-in cutoff.
- `services/attendance_background_worker.py` — auto-checkout sweep + missed check-in reminder delay.
- `features/shifts/service.py` — `late_by` computation in the roster/shift listings.
- Cron scripts: `scripts/attendance_sweeper.py`, `scripts/checkin_reminder_cron.py`,
  `scripts/send_checkout_reminders.py`, `scripts/auto_close_attendance.py`.

### Standalone scripts note
The cron scripts run outside the gunicorn app, so they import `app.models`
(now including `OfficeLocation`) to register every table for FK resolution, and
`app/features/redmine/__init__.py` no longer eagerly imports `routes` (that caused
a circular import when `shift_time` was imported first).

## API
`ShiftDefinitionCreate` / `ShiftDefinitionUpdate` / `ShiftDefinitionResponse`
and `GET /api/shifts/definitions` expose camelCase fields:
```
autoCheckoutEnabled, autoCheckoutGraceMinutes, graceMinutes,
checkinReminderDelayMinutes, checkoutReminderGraceHours,
earlyCheckinLimitMinutes, checkinCutoffMinutes, halfDayMinutes
```
- `POST /api/shifts/definitions` — omitted fields take defaults.
- `PUT /api/shifts/definitions/{shift_id}` — partial update; only sent fields change
  (`ShiftDefinitionUpdate` + `model_dump(exclude_unset=True)`).

## Migration
`core/database.py::_run_migrations()` adds the columns, **backfills from the
current company row** so behavior is unchanged on first deploy, then enforces
`NOT NULL` + defaults. Idempotent (guarded by column existence).

## Behaviour change to communicate
Existing shifts move from a variable "gap to 22:00" auto-checkout grace to a fixed
**60 min** after shift end. Admins can adjust per shift.

## Deprecated (company-level)
These `system_settings` columns are no longer read for timing and are superseded
by the shift columns:
`auto_checkout_enabled`, `auto_checkout_cutoff_time`, `grace_minutes`,
`checkin_reminder_delay_minutes`, `checkout_reminder_grace_hours`.
They are retained in the DB for compatibility; the settings API still returns
them but they no longer affect attendance. Plan to drop after one release.

## Frontend
- Add the 8 fields to the shift-definition create/edit form (toggle + numeric inputs).
- The toggle/limits apply per shift; no company-level fallback to display.
