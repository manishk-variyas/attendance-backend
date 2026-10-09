# Feature Plan — State-Level Holidays

**Status:** Planned
**Goal:** A holiday should apply only to employees in that state. National
holidays still apply to everyone.

---

## Background

**About holidays**
- A holiday is a day the company is closed or gives off.
- Some holidays are for the whole country (national).
- Some holidays are only for one state.
- India has 28 states and 8 union territories. Each one can have its own holidays.
- So two people can have different holiday lists in the same year.

**About our people**
- Our employees work from different states.
- An employee in Telangana should not get a Maharashtra-only holiday.
- An employee in Maharashtra should get Maharashtra holidays.
- Everyone should get national holidays.

**About the system today**
- We store holidays in one `holidays` table.
- That table has a `region` column, but we do not use it.
- Today every holiday is shown to everyone.
- We do not store which state an employee is in.
- So we cannot limit a holiday to one state.

**Why we need this now**
- Wrong holidays confuse people.
- Holidays affect attendance, leave, shifts, and comp-off.
- Google only gives national holidays, not state holidays.
- Payroll and compliance also need the state later.

---

## Use cases

1. Employee in Telangana logs in → sees national + Telangana holidays only.
2. Employee in Uttar Pradesh → sees national + UP holidays only.
3. Admin uploads a Maharashtra holiday with region = `IN-MH` → only Maharashtra
   employees see it.
4. Admin uploads a national holiday with region empty → everyone sees it.
5. When creating shifts, we skip only the employee's own state holidays.
6. When creating WFH, we skip only the employee's own state holidays.
7. If someone works on their own state holiday → comp-off is credited.
8. If someone works on another state's holiday (not theirs) → it is a normal
   working day, no comp-off.
9. Attendance holiday count matches the employee's state.

---

## 1. Current workflow (today)

**The `holidays` table**

| Column | Meaning |
|---|---|
| `id` | Row id |
| `country_code` | Country, e.g. `IN` |
| `region` | Meant for a state, but today holds `'IN'` or empty (not used) |
| `holiday_date` | The date |
| `holiday_name` | e.g. `Diwali` |
| `holiday_type` | `GAZETTED` or `RESTRICTED` |
| `is_national` | `true` = national holiday |

**How holidays are added today**

1. Admin downloads a template:
   `GET /api/leaves/holidays/template?year=2026&country=IN`.
   - It is filled from the Google "Holidays in India" calendar (ICS), or a small
     built-in list if Google fails.
2. Admin reviews/edits the Excel and uploads it:
   `POST /api/leaves/holidays/upload`.
   - Rows are matched by `(country_code, holiday_date, holiday_name)` and
     inserted or updated.
3. Admin can list and delete:
   `GET /api/leaves/holidays`, `DELETE /api/leaves/holidays/{id}`.

**How holidays are used today**

- They apply to **everyone** (there is no state).
- Used by: analytics (holiday count), shift roster, shift creation (skip),
  WFH creation (skip), comp-off for working a holiday, and leave validation.

**Why this is a problem**

- A holiday has no state that actually scopes it.
- The system does not know which state an employee belongs to.
- So we cannot say "this holiday is only for Maharashtra".

## 2. The plan (3 parts)

1. **A master `states` table** — one place that lists all state codes.
2. **Link each employee to a state** — from their office, with an optional
   manual override.
3. **Scope holidays by state** — a holiday applies based on the employee's state.

---

## 3. Master table — `states`

```sql
CREATE TABLE IF NOT EXISTS states (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_code VARCHAR(2)  NOT NULL,        -- country code, e.g. 'IN'
    country_state_code         VARCHAR(10) NOT NULL UNIQUE, -- ISO code, e.g. 'IN-UP'
    gst_code     VARCHAR(2),                  -- GST state code, e.g. '09'
    state_name         VARCHAR(100) NOT NULL,       -- 'Uttar Pradesh'
    state_type         VARCHAR(20) NOT NULL DEFAULT 'state',  -- 'state' or 'union_territory'
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **`code`** is the main key. Every feature uses this.
- **`gst_code`** is added now because payroll (Professional Tax) needs it later.
- **`country_code`** is there so we can add other countries later.

### Seed data (India — 28 states + 8 union territories)

We add the rows once. If a row already exists, we skip it.

| country_state_code | gst_code | state_name | state_type |
|---|---|---|---|
| IN-AP | 37 | Andhra Pradesh | state |
| IN-AR | 12 | Arunachal Pradesh | state |
| IN-AS | 18 | Assam | state |
| IN-BR | 10 | Bihar | state |
| IN-CG | CG | 22 | Chhattisgarh | state |
| IN-GA | GA | 30 | Goa | state |
| IN-GJ | GJ | 24 | Gujarat | state |
| IN-HR | HR | 06 | Haryana | state |
| IN-HP | HP | 02 | Himachal Pradesh | state |
| IN-JH | JH | 20 | Jharkhand | state |
| IN-KA | KA | 29 | Karnataka | state |
| IN-KL | KL | 32 | Kerala | state |
| IN-MP | MP | 23 | Madhya Pradesh | state |
| IN-MH | MH | 27 | Maharashtra | state |
| IN-MN | MN | 14 | Manipur | state |
| IN-ML | ML | 17 | Meghalaya | state |
| IN-MZ | MZ | 15 | Mizoram | state |
| IN-NL | NL | 13 | Nagaland | state |
| IN-OD | OD | 21 | Odisha | state |
| IN-PB | PB | 03 | Punjab | state |
| IN-RJ | RJ | 08 | Rajasthan | state |
| IN-SK | SK | 11 | Sikkim | state |
| IN-TN | TN | 33 | Tamil Nadu | state |
| IN-TS | TS | 36 | Telangana | state |
| IN-TR | TR | 16 | Tripura | state |
| IN-UP | UP | 09 | Uttar Pradesh | state |
| IN-UT | UT | 05 | Uttarakhand | state |
| IN-WB | WB | 19 | West Bengal | state |
| IN-AN | AN | 35 | Andaman and Nicobar Islands | union_territory |
| IN-CH | CH | 04 | Chandigarh | union_territory |
| IN-DH | DH | 26 | Dadra and Nagar Haveli and Daman and Diu | union_territory |
| IN-DL | DL | 07 | Delhi | union_territory |
| IN-JK | JK | 01 | Jammu and Kashmir | union_territory |
| IN-LA | LA | 38 | Ladakh | union_territory |
| IN-LD | LD | 31 | Lakshadweep | union_territory |
| IN-PY | PY | 34 | Puducherry | union_territory |

> Check the ISO and GST codes against the official list before adding them.
> A few codes changed over the years.

---

## 4. Link employee to state

We add one column to two tables:

| Table | New column | Why |
|---|---|---|
| `office_locations` | `state_code` | The office is in a state |
| `employee_master` | `state_code` | Optional override (for remote staff) |

**How we find an employee's state:**
Use the employee's own `state_code` if set. Otherwise, use the state of the
office they are linked to (`employee_master.location_id`).

We then fill these in (backfill): set each office's state, and give employees
their office's state.

---

## 5. How holiday scoping works

- `holidays.region` = the state `code`. Empty means "all states".
- We also clean the old wrong `'IN'` values (change them to empty for national
  holidays).

**A holiday applies to an employee if:**
```
is_national = true   OR   region is empty   OR   region = employee_state
```

---

## 6. What will be affected

**Database**
- New table: `states`.
- New columns: `office_locations.state_code`, `employee_master.state_code`.
- `holidays.region` gets a clear meaning (state code; empty = all states).

**Backend code**
- Analytics holiday list — `app/features/analytics/service.py`.
- Shift roster holidays — `app/features/shifts/routes.py`.
- **Shift creation (skip holidays)** — `app/features/shifts/routes.py`
  (`skipHolidays`). It must skip only the employee's state holidays.
- **WFH creation (skip holidays)** — `app/features/wfh/routes.py` +
  `app/features/wfh/service.py` (`skip_holidays`). Same: use the employee's state.
- **Shift warnings** — `app/features/shifts/service.py` (holiday warning).
- **Comp-off for working a holiday** — `app/features/attendance/routes.py`
  (`_credit_comp_off_if_holiday`). It must use the employee's state holiday.
- **Comp-off reversal on delete** — `app/features/attendance/admin_routes.py`.
- **Comp-off balance (accrual)** — `app/features/leaves/services/balance_utils.py`.
- Leave validation — `app/features/leaves/services/leave_service.py`.
- Holiday upload/template/list — `app/features/leaves/routes.py` +
  `leave_service.py`.
- New small `states` list API (like the `countries` API).

**Every place that reads holidays (checklist)**

| # | Where | File | What it does | Needs state? |
|---|---|---|---|---|
| 1 | Analytics holiday set | `analytics/service.py` | Holiday count | Yes |
| 2 | Shift roster | `shifts/routes.py` | Marks holidays | Yes |
| 3 | Shift create (skip) | `shifts/routes.py` | Skips holidays | Yes |
| 4 | Shift warnings | `shifts/service.py` | Holiday warning | Yes |
| 5 | WFH create (skip) | `wfh/routes.py`, `wfh/service.py` | Skips holidays | Yes |
| 6 | Comp-off credit | `attendance/routes.py` | Comp-off for holiday work | Yes |
| 7 | Comp-off reverse | `attendance/admin_routes.py` | Reverse comp-off | Yes |
| 8 | Comp-off balance | `leaves/services/balance_utils.py` | CO accrual | Yes |
| 9 | Leave validation | `leaves/services/leave_service.py` | Holiday classification | Yes |
| 10 | Holiday CRUD | `leaves/routes.py` | List/upload/template | `region` = state |

**Endpoints affected**
- `/api/analytics/attendance/*` — holiday count per state.
- `/api/shifts/range`, the roster, and **shift create** — holidays per state.
- `/api/wfh/*` create — holiday skip per state.
- `/api/attendance/check-out` — comp-off for a state holiday.
- `/api/leaves/validate` — holiday check per state.
- `/api/leaves/holidays*` — `region` now means state.

**Frontend (later)**
- Employee form — add a State field.
- Office/location form — add a State field.
- Holiday upload — the Region column means state.
- Any holiday filter/dropdown — list states.

**Not affected**
- Working days (still come from shifts).
- Attendance punches, sessions, tickets, rate limiting.

---

## 7. Code we need to update

| Where | File | Change |
|---|---|---|
| Analytics holiday list | `app/features/analytics/service.py` (`_holiday_set`) | Add state. Use the person's state for `me`/`member`. Use each member's own state for `team`. |
| Shift roster holidays | `app/features/shifts/routes.py` (holiday query) | Filter by the employee's state |
| Shift create (skip holidays) | `app/features/shifts/routes.py` (`skipHolidays`) | Skip only the employee's state holidays |
| Shift warnings | `app/features/shifts/service.py` | Use the employee's state holiday |
| WFH create (skip holidays) | `app/features/wfh/routes.py`, `wfh/service.py` | Skip only the employee's state holidays |
| Comp-off credit | `app/features/attendance/routes.py` (`_credit_comp_off_if_holiday`) | Credit only for the employee's state holiday |
| Comp-off reverse | `app/features/attendance/admin_routes.py` | Same state check when reversing |
| Comp-off balance | `app/features/leaves/services/balance_utils.py` | Same state check for CO accrual |
| Leave validation | `app/features/leaves/services/leave_service.py` (`validate_leave_dates`) | Mark a day as holiday using the employee's state |
| Holiday upload/template | `app/features/leaves/routes.py` + `leave_service.py` | The `region` column is already there. Just treat it as the state code. |

**Note:** working days still come from shifts (no change). State affects the
holiday count, the roster, shift/WFH creation, comp-off, and leave validation.

---

## 8. How we create it (migration)

We use the same pattern as `app/core/database.py::_run_migrations()`:

- Create the `states` table.
- Add the seed rows.
- Add `state_code` to `office_locations`.
- Add `state_code` to `employee_master`.
- Backfill office states, employee states, and clean holiday `region`.

We do not use Alembic. This matches how the app already works.

---

## 9. How we test it

- Two employees in different states see different holidays.
- National holidays show for everyone.
- Old date ranges give the same result as before (no regression).
- Leave validation marks state holidays per employee.
- The roster marks holidays per employee state.

---

## 10. Choices we made

| # | Question | Choice |
|---|---|---|
| 1 | Table name | `states` |
| 2 | State code | ISO code (`IN-UP`) plus a short code |
| 3 | Employee state | From office, with an optional override |
| 4 | Other countries | Ready later; add India now |
| 5 | Team analytics | Each member uses their own state |

---

## 11. Steps

- **Step 1 (this doc):** `states` table + employee/office state + holiday
  scoping + update the code that reads holidays.
- **Step 2 (later):** leave rules per state.
- **Step 3 (later):** payroll tax per state (Professional Tax), using `gst_code`.

---

## Outcomes

After this feature:

- Each employee sees the **correct holidays** for their state.
- The **holiday count** in attendance analytics is correct per state.
- **Leave validation** uses the right holidays for each employee.
- **Shift creation** does not skip the wrong holidays.
- **WFH creation** does not skip the wrong holidays.
- **Comp-off** is credited only for real (own-state) holidays.
- We get **one master state list** to reuse for payroll and compliance later.
- Admin can **upload holidays by state** easily.

---

## Implementation status

**Done (Phase 1):**
- `states` table + India seed (36) + admin CRUD API (`/api/states`).
- `holidays.region` = state code; FK to `states(code)`; unique index; timestamps.
- `office_locations.state_code` + `employee_master.state_code` (+ backfill + FKs).
- Shared helper `app/utils/holiday_scope.py` (`employee_state`,
  `holiday_scope_sql`, `find_holiday`, `holiday_dates_in_range`).
- Scoped by state in: analytics holiday count, shift roster, shift creation
  (skip), shift warnings, WFH creation (skip), comp-off credit, leave validation.
- Holiday upload validates `region` against `states.code`.

**Not needed:** comp-off reverse/balance don't query holidays (driven by the
stored flag), so no change.

**Verified:** UP vs TS employees get different holidays; roster shows state
holidays correctly; analytics regression 76/76.

