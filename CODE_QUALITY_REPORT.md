# Code Quality Report — Attendance Backend

---

## 1. Code Duplication

### 1.1 Authorization Role Checks (13 occurrences, 5 files)

Same 3-line guard pattern copy-pasted across the codebase instead of using the existing `require_admin_or_pm` dependency.

```
if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
    raise HTTPException(status_code=403, detail="...")
```

| File | Line |
|---|---|
| `features/leaves/routes.py` | 39, 56, 78, 207, 228, 256 |
| `features/shifts/routes.py` | 487 |
| `features/employees/routes.py` | 155 |
| `features/attendance/routes.py` | 864, 970 |
| `features/auth/dependencies.py` | 82 |

**Fix:** `require_admin_or_pm` already exists at `dependencies.py:80` but is almost never used. Routes duplicate the check inline instead of using `_:None = Depends(require_admin_or_pm)`.

---

### 1.2 Redmine Project Scoping (8 occurrences, 4 files)

The pattern "fetch current user from Redmine → get their projects → fetch target user → get their projects → check intersection" repeats with near-identical code:

| File | Line(s) | Context |
|---|---|---|
| `features/leaves/routes.py` | 80–96 | Team leave history |
| `features/leaves/services/leave_service.py` | 255–267 | `get_user_leave_history` |
| `features/leaves/services/leave_service.py` | 436–457 | `approve_leave` |
| `features/leaves/services/leave_service.py` | 493–515 | `reject_leave` |
| `features/shifts/routes.py` | 391–408 | `/range` endpoint |
| `features/shifts/routes.py` | 510–534 | `/history` endpoint |
| `features/employees/routes.py` | 161–176 | `/my-team` |
| `features/attendance/routes.py` | 868–886 | `/project/{id}` |

There are **30 calls** to `redmine_service.get_projects_for_user()` across the codebase.

**Fix:** Extract into a shared `ensure_project_access(current_user, target_email)` async function.

---

### 1.3 Timezone Safe-Zone Helper (4+ occurrences)

The same `try/except ZoneInfo` fallback pattern:

```python
try:
    tz = ZoneInfo(tz_str)
except Exception:
    tz = ZoneInfo("Asia/Kolkata")
```

| File | Line(s) |
|---|---|
| `features/attendance/routes.py` | 59–63 |
| `features/attendance/admin_routes.py` | 25–29 |
| `features/shifts/service.py` | 398–402, 410–413 |
| `features/leaves/services/leave_service.py` | 168–171, 182–186 |

**Fix:** Single `safe_zone(tz_str: str) -> ZoneInfo` utility.

---

### 1.4 Geofence SQL (3 copies)

The PostGIS geofence query is defined verbatim in two files:

| File | Line(s) |
|---|---|
| `features/attendance/routes.py` | 30–36 |
| `features/location/routes.py` | 15–21, 23–28 |

**Fix:** Move to a shared SQL constants module for features that use PostGIS.

---

### 1.5 Response Serialization (8 different helpers)

Every feature invents its own pattern for converting models to dicts. No two use the same convention:

| File | Helper |
|---|---|
| `models/attendance.py` | `to_dict()` — camelCase keys |
| `features/attendance/routes.py` | `_to_response(db, a)` |
| `features/attendance/admin_routes.py` | `_enrich()`, `_to_response()`, `_to_response_with_email()` |
| `features/shifts/service.py` | `_serialize(shift)`, `_serialize_def(sd)` |
| `features/leaves/admin_routes.py` | `_to_response(b)` |
| `features/employees/routes.py` | `_employee_to_dict(e)` |
| `features/location/admin_routes.py` | `_to_response(loc)` |
| `features/shifts/schemas.py` | Pydantic `from_attributes` |

---

## 2. File Size

| File | Lines | Problem |
|---|---|---|
| `features/attendance/routes.py` | **1061** | Check-in, check-out, history, status, project scoping — all in one route file with zero service layer |
| `features/shifts/service.py` | **760** | CRUD, validations, team logic, enrichment, serialization, definitions — god service |
| `features/employees/routes.py` | **726** | Employee CRUD, teams, onboarding, disonboarding, assignment |
| `features/shifts/routes.py` | **702** | Auth, CRUD, definitions, date ranges, bulk ops, team history |
| `features/leaves/services/leave_service.py` | **567** | Apply, approve, reject, emergency, stats, team, Excel upload |
| `features/attendance/admin_routes.py` | **459** | Admin CRUD, enrichment, virtual records, leave overlay |

---

## 3. Separation of Concerns

### 3.1 Business Logic in Routes (attendance)

`features/attendance/routes.py` has **no service layer**. All business logic lives directly in route handlers:
- Check-in logic: **200+ lines** (lines 156–360)
- Check-out logic: **116 lines** (lines 363–479)
- Complete attendance: **106 lines** (lines 482–588)
- Today status: **158 lines** (lines 649–807)
- Date validation, leave overlap checks, shift selection, late detection, geofencing, DB mutations — all inline

### 3.2 SQL in Business Service

`features/shifts/service.py` calls `db.query()` directly (lines 39, 54, 69, 694, 701) while also delegating to `self._svc(db)` (the PG database service). Three different data-access layers in one file.

### 3.3 FastAPI Exceptions in Service Layer

`features/leaves/services/leave_service.py` raises `fastapi.HTTPException` directly from the service layer. Service classes should be HTTP-agnostic — route handlers should catch domain exceptions and convert them to HTTP responses.

### 3.4 DB Queries in Routes

Direct database queries bypassing any service:
- `shifts/routes.py:133,137` — raw `db.query(ShiftDefinition)`, `db.query(Attendance)` inline in route
- `employees/routes.py:31` — `db.query(EmployeeMaster)` in route
- `leaves/routes.py:90` — `leave_service.leave_db.db.query(EmployeeMaster)` in route (our new code)

---

## 4. Consistency

### 4.1 Two Competing Service Layers

| Layer | Location | Examples |
|---|---|---|
| Business Services | `features/*/services/` or `features/*/service.py` | `LeaveBusinessService`, `ShiftService`, `RedmineService` |
| Database Services | `services/database/` | `ShiftService` (**same name!**), `LeaveService`, `LeaveBalanceService` |

The two layers share names, overlap in responsibility, and confuse the codebase flow.

### 4.2 Dependency Injection

| Service | How It's Created | Testable? |
|---|---|---|
| `LeaveBusinessService` | Proper DI via `get_leave_business_service` | Yes |
| `ShiftService` | Module-level singleton: `shift_service = ShiftService()` at line 760 | No |
| `RedmineService` | Module-level singleton: `redmine_service = RedmineService()` at line 462 | No |

### 4.3 HTTP Status Codes

Some routes use `status.HTTP_403_FORBIDDEN`, others use raw `403`:

| Style | Files |
|---|---|
| `status.HTTP_403_FORBIDDEN` | `shifts/routes.py`, `employees/routes.py` |
| Raw `403` | `leaves/routes.py`, `leaves/services/leave_service.py`, `attendance/admin_routes.py` |

### 4.4 Lazy Imports Inside Functions

Imports nested inside route handlers instead of at module top:

| File | Line | Import |
|---|---|---|
| `shifts/routes.py` | 132 | `from app.models.shift_definition import ShiftDefinition` |
| `shifts/routes.py` | 137 | `from app.models.attendance import Attendance` |
| `shifts/routes.py` | 409–410 | `from app.models.employee_master import EmployeeMaster` + `BaseService` |
| `auth/dependencies.py` | 101–102 | `from app.models.employee_master import EmployeeMaster` + `BaseService` |

### 4.5 Feature Directory Structure

Inconsistent layout across features:

| Feature | Has `services/`? | Has `schemas/`? | Has `admin_routes.py`? |
|---|---|---|---|
| `leaves/` | Yes (subdir) | Yes (subdir) | Yes |
| `shifts/` | No (`service.py` at top) | No (`schemas.py` at top) | No |
| `attendance/` | No | No (`schemas.py` at top) | Yes |
| `employees/` | No | No (`schemas.py` at top) | No |
| `location/` | No | Yes (subdir) | Yes |
| `redmine/` | No (`service.py` at top) | No (`schemas.py` at top) | No |

---

## 5. Models

### 5.1 EmployeeStatus is Not an Enum

`models/employee_master.py:8–11` uses a plain class with string constants instead of a `str, enum.Enum`:

```python
class EmployeeStatus:
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
```

Compare with `models/leave.py:9–19` which properly uses `str, enum.Enum` for `LeaveType` and `LeaveStatus`.

### 5.2 Attendance.to_dict() Inconsistency

`models/attendance.py:53–82` returns date columns (`attendance_date`, `created_at`, `updated_at`) as ISO strings via `.isoformat()` but `check_in_time` / `check_out_time` (also DateTime columns) use the same pattern. The inconsistency is that `server_received_at` (line 76) also uses `.isoformat()`, but `modified_by` (line 77) returns the raw string value, and `check_in_lat` / `check_in_lng` (lines 59–60, 63–64) call `float()`.

No practical bug here — Pydantic response models handle coercion — but it's structurally messy.

---

## 6. Minor / Cleanup Issues

| # | Issue | File | Line |
|---|---|---|---|
| 1 | **Duplicate import** — `from app.models.shift import Shift` imported at line 16 and again at line 22 | `features/attendance/routes.py` | 16, 22 |
| 2 | **Commented-out dead code** — 22 lines of old holiday upload logic | `features/leaves/routes.py` | 122–144 |
| 3 | **Commented-out dead code** — "OLD LOGIC (BACKUP)" block | `features/redmine/service.py` | 186–191 |
| 4 | **Duplicate import** — `from fastapi import UploadFile, File` at both line 1 (inside `from fastapi import`) and line 111 (separate import) | `features/leaves/routes.py` | 1, 111 |
| 5 | **Commented-out role check** — `roles = current_user.get("roles", [])` followed by commented guard | `features/leaves/routes.py` | 122–127 |

---

## 7. Security

### 7.1 Hardcoded Secrets

`core/config.py` contains hardcoded credentials as string defaults:

| Field | Line | Value |
|---|---|---|
| `KEYCLOAK_CLIENT_SECRET` | 29 | `"best-practice-secret-12345"` |
| `SECRET_KEY` | 33 | `"change-me-in-production-use-strong-random-key"` |
| `REDMINE_API_KEY` | 55 | `"c2ffc5526ca3ec90d0359742c48aee46f6ec1688"` |
| `MINIO_ACCESS_KEY` | 59 | `"minioadmin"` |
| `MINIO_SECRET_KEY` | 60 | `"minioadmin"` |

These should come from environment variables with no hardcoded defaults. The current pattern makes accidental commit exposure possible.

---

## 8. Infrastructure

### 8.1 No Test Files

Zero test files exist anywhere in `apps/backend/`. No `test_*.py`, no `tests/` directory, no `conftest.py`, no pytest configuration.

### 8.2 No Migration Framework

`core/database.py:34–72` uses inline SQL in `_run_migrations()` instead of Alembic. Every schema change adds another `DO $$ BEGIN IF NOT EXISTS ...` block. No rollback support, no migration history, no `alembic/versions/` tracking.

### 8.3 In-Memory Session Store

Sessions stored in `dict` — lost on every server restart. (Already known per project notes.)

---

## 9. Error Handling

### 9.1 Broad Exception Swallowing

| File | Line | Issue |
|---|---|---|
| `employees/routes.py` | 127–130 | `except Exception: pass` — silently swallows all Redmine/Keycloak errors during list operations |
| `leaves/routes.py` | 161–167 | Catches `HTTPException` then catches all `Exception` and re-raises as 500 — loses traceback |
| `redmine/routes.py` | 88 | `raise HTTPException(status_code=400, detail=str(e))` — leaks raw exception message to client |

### 9.2 No Global Exception Handler

No custom exception classes, no `@app.exception_handler` for 400/422/500 errors beyond rate limiting.

### 9.3 Typos in User-Facing Messages

| File | Line | Text |
|---|---|---|
| `features/attendance/admin_routes.py` | 446 | `"Attendance h tellrecord not found"` — **already fixed** |

---

## 10. Summary: Priority Matrix

| Priority | Issue | Effort | Impact |
|---|---|---|---|
| **High** | Extract authorization + Redmine scoping into shared utility | 1 hr | Eliminates 200+ lines of duplication, makes every route 3 lines shorter |
| **High** | Move attendance logic from routes to AttendanceService | 3–4 hrs | Testable check-in/check-out, readable routes, prevent regressions |
| **Medium** | Convert `shift_service` and `redmine_service` from singletons to DI | 1–2 hrs | Enables testing, consistent with `LeaveBusinessService` |
| **Medium** | Add Alembic for DB migrations | 1 hr | Trackable, rollback-able schema changes |
| **Medium** | Add test suite (at minimum: check-in, leave apply, shift create) | 4–6 hrs | Safety net for a payroll/attendance system |
| **Low** | Remove hardcoded secrets from config.py | 15 min | Security hygiene |
| **Low** | Clean up: dead code, duplicate imports, lazy imports | 15 min | Readability |
| **Low** | Standardize status code constants (`status.HTTP_403` not `403`) | 30 min | Consistency |
| **Low** | Move `safe_zone()` and geofence SQL to shared utils | 30 min | DRY |
| **Low** | Extract `fastapi.HTTPException` from service layer | 1 hr | Proper layering |
| **Low** | Make `EmployeeStatus` a real enum | 5 min | Consistency with LeaveType/LeaveStatus |

---

## 11. Performance — N+1 Queries

### 11.1 `get_team_shifts` — 3 DB hits per shift row

`features/shifts/service.py:379-387`

```python
for s in shifts:
    emp = db.query(EmployeeMaster).filter(...).first()          # PER SHIFT
    sd  = db.query(ShiftDefinition).filter(...).first()          # PER SHIFT
    leave = db.query(Leave).filter(...).first()                  # PER SHIFT
```

**Impact:** 50 employees x 250 shifts/year = 12,500 rows → **37,500 extra queries**. Each could be one batch `WHERE id IN (...)` / `WHERE shift_code IN (...)`.

`EmployeeMaster` is re-queried per shift even though only distinct user emails exist. Same in `shifts/service.py:464` — re-queries per leave.

### 11.2 `get_shift_history_by_email` — same N+1

`features/shifts/service.py:243-250`

Same ShiftDefinition + Leave per-shift pattern as 11.1, applied to personal history.

### 11.3 `admin_list_attendance` — 5 N+1 per attendance row

`features/attendance/admin_routes.py:357-392`

```python
for record in records:
    _enrich(db, record)           # → EmployeeMaster, OfficeLocation, ShiftDefinition queries
    _to_response_with_email(...)  # → EmployeeMaster, Shift, Leave, EmployeeMaster queries
```

Each attendance record triggers 5–6 separate DB queries. 10K records = **50K+ queries**.

### 11.4 `get_project_attendance` and `get_my_projects_attendance` — identical N+1

`features/attendance/routes.py:903-941` and `:1011-1043`

Per attendance record: EmployeeMaster, Shift, Leave, EmployeeMaster (endedBy) queries — 4 per row.

### 11.5 `get_pending_leaves` — LeaveBalance per leave

`features/leaves/services/leave_service.py:549`

```python
for r in results:
    balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=r.keycloak_user_id)  # PER LEAVE
```

200 pending leaves = 200 individual `LeaveBalance` queries. Should be one `WHERE keycloak_user_id IN (...)` batch.

### 11.6 `_collect_warnings` — Holiday + Leave query per day

`features/shifts/service.py:35-52`

Each day in a shift range queries Holiday and Leave individually. A 30-day shift range = 60 queries just for warnings.

---

## 12. Performance — Redmine HTTP Call Volume

Every `get_projects_for_user()` makes **2 internal HTTP calls** (user lookup + projects list). All calls are sequential. At ~200ms per call:

### 12.1 Catastrophic: `get_visible_leave_users` — 304 HTTP calls

`features/leaves/services/leave_service.py:289-297`

```python
for u in all_users:                                                   # 100 users (API cap)
    tr_user = await redmine_service.get_user_by_email(u["email"])     # HTTP per user
    tr_projects = await redmine_service.get_projects_for_user(tr_id)  # 2 HTTP per user
```

**Formula:** 1 + 1 + 2 + U x (1 + 2) = 4 + 3U. With U=100: **304 sequential HTTP calls → 60+ seconds**. This is called by `GET /leaves/users`.

Also, `get_all_users()` at `redmine/service.py:110` hardcodes `limit=100`, so any org over 100 users gets truncated silently.

### 12.2 Team Endpoints: 8 HTTP calls per request

| Endpoint | Formula | P=5 | Impact |
|---|---|---|---|
| `/shifts/history?team=true` | 3 + P | **8** | 1.6s overhead |
| `/leaves/history?team=true` | 3 + P | **8** | 1.6s overhead |
| `/attendance/my-projects` | 3 + P | **8** | 1.6s overhead |
| `/employees/my-team` | 3 + 2P | **13** | 2.6s overhead (fetches project members **twice**!) |

### 12.3 Approve/Reject/View-Other: 6 HTTP calls

`approve_leave`, `reject_leave`, `get_user_leave_history`, `/shifts/range` — each does:
1. PM Redmine lookup (1 HTTP)
2. PM projects (2 HTTP)
3. Target user Redmine lookup (1 HTTP)
4. Target user projects (2 HTTP)
5. Then computes intersection in Python

---

## 13. Performance — No Pagination / Unbounded Queries

### 13.1 `get_team_shifts` — no LIMIT, optional date filters

`features/shifts/service.py:363`

```python
shifts = query.order_by(Shift.date.desc()).all()   # ← NO LIMIT
```

If `from_date`/`to_date` not provided, loads **every shift for every team member**. Combined with 3 N+1 queries per row (Section 11.1): 300 employees x 250 shifts = 75K rows driving 225K queries.

### 13.2 `get_team_leaves` — limit/skip ignored in team mode

`features/leaves/routes.py:96` passes no limit to the service. `features/leaves/services/leave_service.py:245` has no LIMIT. The route accepts `limit`/`skip` query params but ignores them entirely for `team=true`.

### 13.3 `admin_list_attendance` — no LIMIT

`features/attendance/admin_routes.py:357` — fetches entire Attendance table when no date filters given. Combined with 5 N+1 queries per row (Section 11.3), this is the single biggest memory/query explosion in the codebase.

### 13.4 `list_employees` — no LIMIT

`features/employees/routes.py:102` — full employee table scan, no pagination.

### 13.5 `get_shift_history_by_email` — no LIMIT

`features/shifts/service.py:226` — loads all of one user's shift history with no limit.

---

## 14. Performance — Missing Database Indexes

### Leave table (`models/leave.py`)

| Column | Has Index? | Queried In |
|---|---|---|
| `keycloak_user_id` (line 26) | Yes | IN clauses, equality |
| `user_email` (line 27) | Yes | IN clauses, equality |
| `approval_status` (line 38) | **No** | Every query: `WHERE approval_status = 'approved'` |
| `start_date` (line 28) | **No** | Range queries: `start_date <= X`, `start_date >= Y` |
| `end_date` (line 29) | **No** | Range queries: `end_date >= X`, `end_date <= Y` |

**Missing composite:** `(keycloak_user_id, approval_status, start_date, end_date)` would cover overlap checks in one index scan.

### Shift table (`models/shift.py`)

| Column | Has Index? | Queried In |
|---|---|---|
| `keycloak_user_id` (line 12) | Yes | IN clauses, equality |
| `user_email` (line 14) | Yes | IN clauses |
| `date` (line 15) | Yes | Range queries |
| `end_date` (line 16) | **No** | Range queries: `end_date >= X` |
| `shift_code` (line 17) | **No** | JOIN with shift_definitions (every N+1 query) |

### Attendance table (`models/attendance.py`)

| Column | Has Index? | Queried In |
|---|---|---|
| `keycloak_user_id` (line 20) | Yes (plus unique constraint) | IN clauses, equality |
| `attendance_date` (line 21) | Yes (plus unique constraint) | Range queries |
| `check_out_time` (line 26) | **No** | `WHERE check_out_time IS NULL` |
| `shift_code` (line 34) | **No** | UPDATE cascades |

### EmployeeMaster table (`models/employee_master.py`)

All frequently-queried columns (`user_email`, `redmine_user_id`, `keycloak_user_id`) have `unique=True`. No issues.

---

## 15. Performance — Inefficient Queries

### 15.1 Per-day INSERT loop (leave creation)

`features/leaves/services/leave_service.py:91-108`

```python
while current_date <= end_date:
    self.leave_db.create(Leave, **leave_doc)   # 1 INSERT + COMMIT per day
    current_date += timedelta(days=1)
```

A 30-day leave = 30 sequential INSERT statements, 30 round-trips to PostgreSQL. Should use `bulk_insert_mappings`.

### 15.2 Leave day-expansion creates inflated result sets

`features/shifts/service.py:461-490` — each approved leave is expanded to one result row per day. A 30-day leave = 30 result records. With 10 team members taking 2x 15-day leaves per year, that adds 300 result rows to an already unbounded query.

### 15.3 Full table scans with no WHERE clause

| File | Line |
|---|---|
| `shifts/service.py:651` | `get_all_shift_definitions()` — all rows, no pagination |
| `employees/routes.py:102` | `list_employees()` — full scan if no filters |
| `attendance/admin_routes.py:357` | `admin_list_attendance()` — full scan if no dates |

---

## 16. Performance — Quick Wins vs. Structural Fixes

| Fix | Effort | Impact |
|---|---|---|
| Add Redmine project members cache table (DB) | 2 hrs | Eliminates **all 304 HTTP calls** from `get_visible_leave_users`, drops every team endpoint from 8–13 HTTP calls to 0. Single highest-ROI change. |
| Batch N+1 queries (EmployeeMaster, ShiftDefinition, Leave) into pre-fetched dicts | 1 hr | Kills 37K queries in `get_team_shifts`, 50K queries in `admin_list_attendance` |
| Add `approval_status` index on `leaves` | 2 min | Every leave query benefits |
| Add `start_date`/`end_date` indexes on `leaves` | 2 min | Range overlap queries go from seq scan to index scan |
| Add `check_out_time` index on `attendance` | 2 min | IS NULL checks go from seq scan to index scan |
| Replace per-day INSERT loop with `bulk_insert_mappings` | 15 min | 30 round-trips → 1 for 30-day leave |
| Enforce `from_date`/`to_date` required for team endpoints | 10 min | Prevents unbounded queries, no DB changes needed |

---

## 17. Summary: Complete Priority Matrix (with Performance)

| Priority | Issue | Effort | Impact |
|---|---|---|---|
| **Critical** | Redmine project members cache (DB table) | 2 hrs | 304 HTTP calls → 0, all team endpoints instant |
| **High** | Extract authorization + Redmine scoping into shared utility | 1 hr | Eliminates 200+ lines of duplication |
| **High** | Batch N+1 queries in `get_team_shifts` and `admin_list_attendance` | 1 hr | Kills 50K+ extra queries per request |
| **High** | Move attendance logic from routes to AttendanceService | 3–4 hrs | Testable, readable, prevents regressions |
| **Medium** | Add missing DB indexes (leave status/dates, shift end_date, attendance check_out_time) | 10 min | Every query benefits |
| **Medium** | Enforce date filters on team endpoints | 10 min | Prevent unbounded queries |
| **Medium** | Replace per-day INSERT loop with bulk insert | 15 min | 30x improvement for multi-day leaves |
| **Medium** | Convert `shift_service` and `redmine_service` from singletons to DI | 1–2 hrs | Enables testing |
| **Medium** | Add Alembic for DB migrations | 1 hr | Trackable schema changes |
| **Medium** | Add test suite (check-in, leave apply, shift create) | 4–6 hrs | Safety net |
| **Low** | Remove hardcoded secrets from config.py | 15 min | Security |
| **Low** | Clean up: dead code, duplicate imports, lazy imports | 15 min | Readability |
| **Low** | Standardize status codes (`status.HTTP_403` not `403`) | 30 min | Consistency |
| **Low** | Move `safe_zone()` and geofence SQL to shared utils | 30 min | DRY |


Critical — Will break at scale
1. get_visible_leave_users — 304 sequential Redmine HTTP calls
leave_service.py:290 loops over every Redmine user, calling get_user_by_email() + get_projects_for_user() per user. With 100 users (already capped): 60+ seconds. This is called from GET /leaves/users — a request that simply times out.
2. N+1 queries in get_team_shifts — 3 DB hits per shift row
shifts/service.py:380-387 queries EmployeeMaster, ShiftDefinition, and Leave individually per shift in a loop. For a team of 50 with 1 year of shifts (~12,500 rows), that's 37,500 extra queries in a single request.
3. admin_list_attendance — unbounded + 5 N+1 per row
admin_routes.py:357-358 fetches all attendance with no pagination, then calls 5 separate DB queries per row for enrichment. 10,000 records = 50,000+ queries.
4. Team endpoints have no pagination
Both get_team_shifts and get_team_leaves ignore limit/skip entirely. No date filters = load every record for every team member into memory.
High — Degrades noticeably under load
5. Per-day INSERT loop for leave creation
leave_service.py:91-108 does one INSERT per day. A 30-day leave = 30 round trips to PostgreSQL. Use bulk_insert_mappings.
6. get_pending_leaves N+1 on LeaveBalance
leave_service.py:549 queries LeaveBalance individually per pending leave. Should be one WHERE keycloak_user_id IN (...).
7. /employees/my-team fetches project members twice
employees/routes.py:169 and :193 make the same get_project_members() calls in two separate loops. Double the HTTP calls.
8. Missing database indexes
No index on Leave.approval_status, Leave.start_date, Leave.end_date, Shift.end_date, Attendance.check_out_time — every status/date/range query is a sequential scan.
Medium — Inefficient, not dangerous
9. _collect_warnings queries Holiday + Leave per day
shifts/service.py:39,43 — 30-day shift range = 60 individual queries for warnings.
10. get_all_users capped at 100
redmine/service.py:110 hardcodes limit=100. An org with 3000 users silently misses 2900 of them in any feature using this.
The biggest quick-win: Add a DB cache table for Redmine project memberships (your own next-steps note). One query instead of 8+ sequential HTTP calls per request. That single change drops the 304-call catastrophe to 0 and makes every team-scoped endpoint fast.