# Attendance ↔ Shift Location Link — Simple Plan

Goal: stop attendance location from drifting away from the shift, without
changing the client request payload.

---

## The idea (one line)
Keep **two** locations on attendance: the **expected** (from the shift) and the
**actual** (from the punch), link the punch to its exact shift, and compute the
display text instead of storing it.

---

## Step 1 — Add columns (safe, nullable)

On `attendance`:

| Column | Type | FK | Meaning |
|---|---|---|---|
| `shift_id` | UUID | → `shifts.id` | the exact roster shift this punch belongs to |
| `actual_location_id` | UUID | → `office_locations.id` | office where the person **actually** was (geofence result) |

`location_id` keeps its meaning = **expected** office (from the shift).
Add both as nullable + FK — nothing breaks.

---

## Step 2 — Backfill existing rows

- `shift_id`: match `attendance` → `shifts` by
  `(keycloak_user_id, attendance_date, shift_code)`.
  If a day has more than one shift, pick the one whose code matches (or leave NULL).
- `actual_location_id`: for `work_location_status = 'OFFICE'` set = `location_id`;
  for `WFH` leave NULL (or nearest office — optional).

---

## Step 3 — Write path (check-in / check-out / manual)

At check-in we already resolve the shift and geofence against lat/lng
(`routes.py:251-269`). Just also store:
- `shift_id = s.id`
- `actual_location_id` = office when the geofence says OFFICE, else NULL

Manual admin entry: derive the same way (or let admin set it).
No new client fields — client still sends only lat/lng.

---

## Step 4 — Read path (responses)

Keep the same response field names, add a few optional ones:

| Field | Source |
|---|---|
| `checkInLocationName` | computed on read from `actual_location_id` + lat/lng (not stored) |
| `shiftId` | `attendance.shift_id` |
| `expectedLocationId` | `attendance.location_id` (the shift's office) |
| `actualLocationId` | `attendance.actual_location_id` |
| `locationMismatch` | `expected != actual` |

Because "expected" is read **live from the shift** (via `shift_id`), editing a
shift's location now shows up automatically — no manual fix-ups.

---

## Step 5 — Test

- Check in inside office → expected == actual, no mismatch.
- Check in from home → actual = NULL, mismatch = false (WFH is expected).
- Change a shift's office → the *expected* view updates; history (actual) stays.
- Old clients: unchanged (extra fields ignored).

---

## Notes
- No Alembic — use the existing `_run_migrations()` pattern in `core/database.py`.
- `check_in_location_name` can stay for audit, but stop relying on it for display.
- Keep it incremental: Step 1+2 first (data), then Step 3 (writes), then Step 4 (reads).
