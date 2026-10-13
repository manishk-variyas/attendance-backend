# Multi-Shift Attendance + Timezone — Design Plan

Status: **Planning (not implemented)**

## 1. Context / problem

- `attendance` is **one row per (user, day)** with a **single** check-in/out →
  it cannot represent **2+ shifts** or **2+ check-ins** on one day.
- `shift_attendance` (per-shift punches) exists but is **unused** and will be
  retired.
- `shifts` (roster) **already** supports multiple shifts per day.
- Timezone: the **shift's timezone** is respected for shift *timing* (start/end,
  late, auto-checkout), but the **day** (`attendance_date`) and analytics day
  boundaries are **hard-coded to `Asia/Kolkata`**.

## 2. Goals

- Support **N shifts/day**, each with its own check-in/out.
- Keep `attendance` as the **daily rollup** so existing APIs don't break.
- A dedicated **per-shift punch** store (single source of truth).
- Correct **day attribution per shift timezone** (not global IST).
- Cover overnight, partial, leave/holiday, manual, auto-checkout, ad-hoc.

## 3. Non-goals (phase 1)

- Multiple in/out **within** one shift (breaks) — phase 2.
- Payroll / overtime engines.
- Per-shift grace config (separate, can piggyback).

---

## 4. Data model

### 4.1 Tables

**Keep unchanged (contract for existing APIs):**
- `shift_master` — shift definitions.
- `shifts` — roster (already many rows/user/day).
- `attendance` — **daily rollup**, `UNIQUE (keycloak_user_id, attendance_date)`.

**New: `attendance_punches`** — one row per shift:

```sql
CREATE TABLE IF NOT EXISTS attendance_punches (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shift_id               UUID UNIQUE REFERENCES shifts(id) ON DELETE SET NULL,
    keycloak_user_id       VARCHAR(255) NOT NULL,
    attendance_date        DATE NOT NULL,            -- shift's LOCAL date (see §5)
    shift_code             VARCHAR(50) REFERENCES shift_master(shift_code),
    -- punch-in
    check_in_time          TIMESTAMPTZ,
    check_in_lat           NUMERIC(10,8),
    check_in_lng           NUMERIC(11,8),
    check_in_location_name VARCHAR(500),
    check_in_location_id   UUID REFERENCES office_locations(id) ON DELETE SET NULL,
    -- punch-out
    check_out_time         TIMESTAMPTZ,
    check_out_lat          NUMERIC(10,8),
    check_out_lng          NUMERIC(11,8),
    check_out_location_name VARCHAR(500),
    check_out_location_id  UUID REFERENCES office_locations(id) ON DELETE SET NULL,
    -- per-shift state
    work_location_status   VARCHAR(20),              -- OFFICE / WFH / ...
    is_late                BOOLEAN NOT NULL DEFAULT false,
    total_hours            NUMERIC(5,2),
    status                 VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    -- audit
    is_manual_entry        BOOLEAN NOT NULL DEFAULT false,
    modified_by            VARCHAR(255),
    remarks                VARCHAR(500),
    is_synced              BOOLEAN NOT NULL DEFAULT false,
    server_received_at     TIMESTAMPTZ,
    is_deleted             BOOLEAN NOT NULL DEFAULT false,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_punches_user_date ON attendance_punches (keycloak_user_id, attendance_date);
```

> One ad-hoc punch (no shift) is allowed per (user, date) via a partial unique
> index: `UNIQUE (keycloak_user_id, attendance_date) WHERE shift_id IS NULL`.

### 4.2 Keys / integrity

| Table | Key | Notes |
|---|---|---|
| `attendance_punches` | `UNIQUE (shift_id)` | one punch per shift |
| `attendance` | `UNIQUE (keycloak_user_id, attendance_date)` | unchanged |

---

## 5. Timezone model

**The shift owns the working day.**

- **Day source of truth = the shift's timezone** → `attendance_date` = the shift's
  **local date** (its `shifts.date`), not `now(IST).date()`.
- **`employee_master.timezone` = display only** — *not* in the day-resolution chain.
- All instants stored as **UTC** (`timestamptz`); already the case.

**Fallback hierarchy for the day (no shift):**
```
shift.timezone  →  office.timezone  →  company.default_timezone  →  Asia/Kolkata
```

**Client timestamps:** `resolve_timestamp` currently assumes a **naive** input is
**UTC** (`shift_time.py:44`). Fix: require tz-aware input, or interpret naive as
the **shift timezone** (not UTC).

**Why:** a USA employee on an India-timezone shift works an *India* shift → the
record belongs to the **India date**. Their physical tz must not move it.

---

## 6. Punch lifecycle

- **Check-in(shift)** — upsert punch by `shift_id` (reject if already checked in
  → `409`). Geofence + `is_late` computed **in the shift tz**.
- **Check-out(shift)** — target punch by `shift_id`; reject if none / no check-in
  (`400`) / checkout before checkin (`400`).
- **Manual create/edit (admin)** — write the punch; recompute rollup.
- **Auto-checkout (sweeper)** — operate on **punches** (open ones), then rollup.
- **After every punch write → recompute the day's `attendance` rollup** in the
  **same transaction**.

---

## 7. Daily rollup rules (`attendance`, one row/day)

Derived from that day's punches (+ shifts / leave / holiday):

| Field | Rule |
|---|---|
| `status` | `present` if any punch present; else `half_day` if any partial; else `on_leave` if approved leave on a shift day; else `holiday` if holiday; else `absent` (scheduled shift with no punch) |
| `check_in_time` | earliest punch-in |
| `check_out_time` | latest punch-out |
| `total_hours` | **sum** of punch hours (null if none completed) |
| `is_late` | **any** punch late |
| `work_location_status` | `OFFICE` if any OFFICE, else `WFH` if any WFH |
| `shift_id` | the shift only when the day has exactly one shift; else NULL |

---

## 8. Edge cases

| # | Case | Resolution |
|---|---|---|
| 1 | 2–3 shifts/day | N punches (keyed by `shift_id`) → 1 rollup |
| 2 | Overnight shift (22:00→06:00) | `attendance_date` = shift `date`; `check_out` may be next day |
| 3 | Ad-hoc punch, no shift | `shift_id` NULL; one per (user, date) via partial unique |
| 4 | Duplicate check-in, same shift | `UNIQUE(shift_id)` → 409 |
| 5 | Check-out with no check-in | 400 |
| 6 | Check-out for wrong shift | resolved by `shift_id` |
| 7 | Leave on a shift day, no punch | rollup `on_leave` (from `leaves`) |
| 8 | Holiday on a shift day, no punch | rollup `holiday` |
| 9 | Checked-in, not out | rollup `present`, `total_hours` null |
| 10 | Late | per punch (shift start + grace, shift tz); rollup = any |
| 11 | Shift edited/deleted after punch | punch keeps history (`ON DELETE SET NULL`); expected office read live via `shift_id` |
| 12 | Admin edits attendance | edits the **punch**; rollup recomputed |
| 13 | Cross-midnight hours | from punch times; may exceed a calendar day |
| 14 | Existing rows | backfill 1 punch per `attendance` row via `shift_id` |
| 15 | Concurrency | `UNIQUE(shift_id)` prevents double punch |
| 16 | Cross-timezone shift | day = shift local date; timing in shift tz |
| 17 | Employee tz ≠ shift tz | employee tz display-only |
| 18 | Naive client timestamp | interpret as shift tz (not UTC) |
| 19 | DST transitions | `ZoneInfo` handles; store UTC |
| 20 | Rollup drift | nightly reconcile job rebuilds rollups from punches |

---

## 9. Blast radius

### A. Writers (change → punch + rollup)
- `features/attendance/routes.py` — `POST /check-in`, `/check-out`, `/shifts/check-in`, `/shifts/check-out`
- `features/attendance/admin_routes.py` — `POST/PUT/DELETE /admin/attendance`
- `services/attendance_background_worker.py` — auto-checkout
- `services/database/shift_service.py` — on shift delete (re-link/clear punch)

### B. Readers (unchanged **if** rollup stays in sync)
- `features/analytics/service.py` — all attendance SQL
- `features/shifts/service.py` — shift history/records
- `features/shifts/routes.py` — "already checked in" checks
- `features/wfh/routes.py` — WFH validation
- `features/leaves/services/leave_service.py` — leave validation
- `features/attendance/routes.py` — `today`, `pending`, `my-attendance(-v1)`, `team-attendance(-v1)`
- `features/attendance/admin_routes.py` — list/get

### C. Endpoints touched
- Attendance: `check-in`, `check-out`, `shifts/check-in`, `shifts/check-out`, `today`, `today/shifts`, `pending`, `project/{id}`, `my-projects`, `my-attendance(-v1)`, `team-attendance(-v1)`
- Admin: `POST/PUT/GET/DELETE /admin/attendance`
- Analytics: `/analytics/attendance/me|team|member` (read rollup — no change)
- Shifts: create/update/delete, `range`, `roster`

### D. Retire
- `models/shift_attendance.py`, `services/attendance/services/shift_attendance_service.py`,
  `models/shift.py` relationship, `get_roster` punch read.

---

## 10. Migration & backfill

1. Create `attendance_punches`.
2. Backfill: for each existing `attendance` row → **one punch** linked via its
   `shift_id` (already populated); `attendance_date` = the row's date.
3. Keep `attendance` rows as-is (already the rollup).
4. Drop `shift_attendance` after cutover.

---

## 11. Rollout phases

1. Create table + backfill (**no behavior change**).
2. **Dual-write**: check-in/out write punch **and** rollup.
3. Switch per-shift reads (roster/calendar) to punches.
4. Add nightly reconcile job.
5. Retire `shift_attendance`.

---

## 12. Open decisions

1. Ad-hoc punch without a shift — allow (partial unique) or require a shift?
2. Multiple punches within a shift (breaks) — phase 1 no, phase 2 yes?
3. Rollup storage — stored (synced) vs computed view? (recommend **stored**).
4. `attendance.shift_id` — keep only for single-shift days, or drop once punches own `shift_id`?
5. `employee.timezone` — keep for display only, or drop?
6. Rollup reconciliation — trigger vs nightly job (recommend **both**: job now, trigger later).
