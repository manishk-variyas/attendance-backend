# Attendance Feature Architecture

## Overview

The Attendance module handles employee check-in/check-out, shift-based attendance, geofencing, and attendance reporting. It's the most complex feature in the codebase with **1714 lines** in a single routes file.

**Key Characteristics:**
- Two coexisting attendance systems (legacy date-based + newer shift-based)
- Heavy timezone handling (Asia/Kolkata default, per-shift overrides)
- Geofencing via PostGIS for office location validation
- Comp-off auto-crediting for holiday work
- Auto-checkout for overnight shifts

---

## Data Models

### Primary Tables

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ATTENDANCE DATA MODEL                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐  │
│  │   attendance     │     │  shift_attendance │     │      shifts         │  │
│  │   (legacy)       │     │  (newer)          │     │  (assignments)     │  │
│  ├─────────────────┤     ├──────────────────┤     ├─────────────────────┤  │
│  │ id (UUID PK)    │     │ id (UUID PK)     │     │ id (UUID PK)       │  │
│  │ keycloak_user_id│     │ shift_id (FK)    │────▶│ keycloak_user_id   │  │
│  │ attendance_date │     │ keycloak_user_id │     │ date               │  │
│  │ check_in_time   │     │ attendance_date  │     │ shift_code (FK)    │  │
│  │ check_in_lat    │     │ check_in_time    │     │ project_id         │  │
│  │ check_in_lng    │     │ check_out_time   │     │ work_location_status│  │
│  │ check_out_time  │     │ total_hours      │     │ status             │  │
│  │ work_location   │     │ status           │     │ location_id (FK)   │  │
│  │ shift_code      │     └──────────────────┘     └─────────┬───────────┘  │
│  │ is_late         │                                        │              │
│  │ total_hours     │                                        │              │
│  │ is_manual_entry │                                        ▼              │
│  │ comp_off_credited│                              ┌─────────────────────┐  │
│  └─────────────────┘                              │  shift_master        │  │
│                                                   │  (definitions)       │  │
│                                                   ├─────────────────────┤  │
│  ┌─────────────────┐                              │ shift_code (PK)     │  │
│  │ office_locations │                              │ shift_name          │  │
│  ├─────────────────┤◀─────────────────────────────│ start_time          │  │
│  │ id (UUID PK)    │                              │ end_time            │  │
│  │ name            │                              │ timezone            │  │
│  │ latitude        │                              └─────────────────────┘  │
│  │ longitude       │                                                       │
│  │ radius_meters   │     ┌──────────────────┐                              │
│  │ geom (PostGIS)  │     │ employee_master   │                              │
│  └─────────────────┘     ├──────────────────┤                              │
│                          │ id (UUID PK)     │                              │
│                          │ keycloak_user_id │                              │
│                          │ location_id (FK) │                              │
│                          │ user_email       │                              │
│                          └──────────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Model Relationships

- **EmployeeMaster** → `location_id` → **OfficeLocation** (employee's assigned office)
- **Shift** → `shift_code` → **ShiftDefinition** (shift timing definition)
- **Shift** → `location_id` → **OfficeLocation** (shift-specific location override)
- **Attendance** → standalone (legacy, linked by user_id + date)
- **ShiftAttendance** → `shift_id` → **Shift** (newer, linked to specific shift)

---

## API Endpoints

### User Endpoints

| Method | Path | Purpose | Lines |
|--------|------|---------|-------|
| POST | `/attendance/check-in` | Check in for the day | 105-322 |
| POST | `/attendance/check-out` | Check out | 325-435 |
| POST | `/attendance/complete` | Complete attendance (next-day) | 438-544 |
| GET | `/attendance/pending` | Get pending attendance actions | 547-602 |
| GET | `/attendance/today` | Get today's attendance status | 605-759 |
| GET | `/attendance/today/shifts` | Get today's shifts with status | 762-861 |
| POST | `/attendance/shifts/check-in` | Check in to specific shift | 883-898 |
| POST | `/attendance/shifts/check-out` | Check out from specific shift | 901-917 |
| GET | `/attendance/my-attendance` | Paginated attendance history | 1509-1565 |
| GET | `/attendance/team-attendance` | Team attendance (admin/PM/PC) | 1568-1639 |
| GET | `/attendance/my-attendance-v1` | V1 attendance via shift_service | 1642-1670 |
| GET | `/attendance/team-attendance-v1` | V1 team attendance | 1673-1714 |

### Admin Endpoints

| Method | Path | Purpose | Lines |
|--------|------|---------|-------|
| POST | `/admin/attendance` | Create attendance manually | 181-263 |
| PUT | `/admin/attendance/{id}` | Update attendance | 266-340 |
| GET | `/admin/attendance/{id}` | Get single attendance | 343-353 |
| GET | `/admin/attendance` | List attendance (filtered) | 356-518 |
| DELETE | `/admin/attendance/{id}` | Delete attendance | 521-543 |

---

## Key Business Logic

### 1. Shift Picking Algorithm (`_pick_shift`)

```python
def _pick_shift(db, shifts, target_date, now):
    """
    Priority order:
    1. Active shift (start_dt <= now <= end_dt)
    2. Next upcoming shift (start_dt > now)
    3. Earliest shift (fallback)
    """
```

**Complexity**: Timezone-aware comparison using shift's own timezone from `shift_master.timezone`.

### 2. Check-in Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CHECK-IN FLOW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Validate date (today or yesterday only)                                 │
│                    │                                                        │
│                    ▼                                                        │
│  2. Check for open attendance (yesterday not closed)                        │
│                    │                                                        │
│                    ▼                                                        │
│  3. Check for existing attendance (idempotent return)                       │
│                    │                                                        │
│                    ▼                                                        │
│  4. Check if on leave (block check-in)                                      │
│                    │                                                        │
│                    ▼                                                        │
│  5. Resolve timestamp (client or server)                                    │
│                    │                                                        │
│                    ▼                                                        │
│  6. Find matching shift (active → upcoming → earliest)                      │
│                    │                                                        │
│                    ▼                                                        │
│  7. Calculate is_late (vs shift_start + grace_minutes)                      │
│                    │                                                        │
│                    ▼                                                        │
│  8. Determine work location (geofence check)                                │
│                    │                                                        │
│                    ▼                                                        │
│  9. Create attendance record                                                │
│                    │                                                        │
│                    ▼                                                        │
│  10. Update shift status → "In Progress"                                    │
│                    │                                                        │
│                    ▼                                                        │
│  11. Update user_locations table                                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3. Geofencing Logic (`geo.py`)

```python
# Uses PostGIS ST_DWithin for radius check
GEOFENCE_SQL = """
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
"""

def determine_work_location(db, user_lat, user_lng, location_id):
    # Returns "OFFICE" if within radius, else "WFH"
```

### 4. Auto-Checkout Logic (in `/today` endpoint)

```python
# If shift has ended and auto_checkout_enabled:
if auto_checkout_enabled and datetime.now(timezone.utc) > cutoff_dt:
    open_att.check_out_time = cutoff_dt
    # cutoff_dt = max(company.auto_checkout_cutoff_time, shift_end)
```

### 5. Comp-Off Crediting

```python
def _credit_comp_off_if_holiday(db, attendance):
    # If check-out completed and date is a holiday → credit comp-off
    holiday = db.query(Holiday).filter(Holiday.holiday_date == attendance.attendance_date).first()
    if holiday:
        credit_comp_off(db, keycloak_user_id, attendance.attendance_date)
```

---

## Data Enrichment Pattern

Both routes.py and admin_routes.py use a pattern of enriching attendance records with related data:

```python
# 1. Batch fetch related entities
emp_map = {e.keycloak_user_id: e for e in employees}
loc_map = {str(l.id): l for l in offices}
sd_map = {s.shift_code: s for s in shift_defs}
shift_map = {(s.keycloak_user_id, s.date): s for s in shifts}
leave_map = {(l.keycloak_user_id, current): l for l in leaves}

# 2. Enrich each record
for record in records:
    data = record.to_dict()
    data["officeName"] = loc_map[record.location_id].name
    data["shiftName"] = sd_map[record.shift_code].shift_name
    data["userName"] = f"{emp.first_name} {emp.last_name}"
    # ... 10+ more fields
```

**Issue**: This logic is duplicated between `routes.py` and `admin_routes.py`.

---

## Timezone Handling

**Default**: `Asia/Kolkata` (IST, UTC+5:30)

**Timezone sources** (in priority order):
1. `shift_master.timezone` (per shift definition)
2. `system_settings.default_timezone`
3. Hardcoded fallback `"Asia/Kolkata"`

**Key functions** (`utils/shift_time.py`):
- `safe_zone(tz_str)` - Resolves timezone with Redmine label mapping
- `shift_end_datetime(date, start_time, end_time, tz)` - Handles overnight shifts
- `resolve_timestamp(client_ts)` - Normalizes client timestamps

**Complexity**: Night shifts crossing UTC midnight require ±1 day query windows.

---

## Caching Strategy

**Location**: `core/cache.py`

**Cached entities**:
- `SystemSetting` (company settings) - 30 second TTL
- `ShiftDefinition` - 30 second TTL per shift_code

**Why**: Hot endpoints (check-in/out) fetch these on every call. They change rarely (admin edits).

**Invalidation**: Manual via `clear_*()` functions called from write paths.

---

## Code Issues & Technical Debt

### Critical Issues

| Issue | Location | Impact |
|-------|----------|--------|
| **1714-line routes file** | `routes.py` | Unmaintainable, mixed concerns |
| **Duplicated enrichment logic** | `routes.py` + `admin_routes.py` | Maintenance burden, inconsistency |
| **Business logic in routes** | `routes.py` lines 105-322 | Untestable without HTTP |
| **Duplicate constants** | `ACTIVE_LEAVE_STATUSES` defined twice | Risk of divergence |

### Code Duplication

```python
# routes.py line 88-102
def _to_response(db, a: Attendance) -> dict:
    data = a.to_dict()
    if a.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == a.location_id).first()
        data["officeName"] = office.name if office else None
    # ...

# admin_routes.py line 51-93
def _to_response_with_email_batched(a, emp_map, loc_map, sd_map, shift_map, leave_map, modified_by_map):
    data = a.to_dict()
    data = _enrich_with_maps(data, a, loc_map, sd_map)
    # ... similar enrichment plus email fields
```

### Missing Abstractions

1. **No repository pattern** - Raw SQLAlchemy queries throughout
2. **No response builder** - Dict construction duplicated
3. **No pagination helper** - `_get_attendance_paginated` is 200 lines
4. **No shift matching service** - `_find_matching_shift` and `_pick_shift` are standalone functions

---

## Dependencies

### Internal Dependencies

```
attendance/
├── auth/dependencies.py          # get_current_user, require_active
├── leaves/services/balance_utils.py  # credit_comp_off
├── leaves/models/leave.py        # Leave model for overlap check
├── shifts/service.py             # shift_service.get_shifts_v1
├── shifts/models/shift.py        # Shift model
├── redmine/sql_service.py        # RedmineSQLService for team lookup
├── redmine/constants.py          # REDMINE_TO_IANA_TZ timezone mapping
├── core/cache.py                 # get_company_settings, get_shift_definition
├── core/database.py              # get_db
├── services/email_service.py     # Missed check-in reminders
├── utils/audit.py                # audit_logger
├── utils/shift_time.py           # Timezone helpers
├── utils/excel.py                # Excel export
└── models/                       # All ORM models
```

### External Dependencies

- `fastapi` - Web framework
- `sqlalchemy` - ORM
- `geoalchemy2` - PostGIS integration (via OfficeLocation.geom)
- `zoneinfo` - Timezone handling

---

## Two Attendance Systems

### Legacy: `Attendance` Model

- **One record per user per date**
- Used by: check-in, check-out, complete, /today, my-attendance
- **No shift linkage** (shift_code stored but no FK)

### Newer: `ShiftAttendance` Model

- **One record per shift** (FK to shifts.id)
- Used by: /shifts/check-in, /shifts/check-out, /today/shifts
- **Tied to specific shift** assignment

### Why Two Systems?

The codebase shows signs of migration:
- `ShiftAttendance` has dedicated service (`ShiftAttendanceService`)
- `/today/shifts` endpoint uses shift-based punches
- `/today` endpoint still uses legacy `Attendance`
- V1 endpoints (`/my-attendance-v1`) delegate to `shift_service`

**Recommendation**: Consolidate to shift-based system, deprecate legacy.

---

## Testing Gaps

1. **No unit tests** for business logic (shift picking, late calculation)
2. **No integration tests** for check-in/out flows
3. **Timezone edge cases** untested (midnight crossing, DST)
4. **Geofencing** requires PostGIS (hard to test without DB)

---

## Refactoring Opportunities

### Immediate (Low Risk)

1. **Extract `_to_response` to shared module** - Eliminate duplication
2. **Move `ACTIVE_LEAVE_STATUSES` to constants.py** - Single source of truth
3. **Extract `_pick_shift` to service** - Testable shift matching

### Medium Risk

4. **Create `AttendanceService`** - Move business logic from routes
5. **Create `AttendanceRepository`** - Encapsulate queries
6. **Create response builder** - Standardized enrichment

### Higher Risk

7. **Split routes.py** - Separate user/admin/shift routes
8. **Consolidate attendance models** - Single system
9. **Add Alembic migrations** - Replace inline SQL in database.py

---

## File Size Analysis

| File | Lines | Concern |
|------|-------|---------|
| `routes.py` | 1714 | **Critical** - Mixed concerns, too large |
| `admin_routes.py` | 543 | High - Duplicated logic |
| `schemas.py` | 161 | OK - Standard schemas |
| `services/shift_attendance_service.py` | 188 | OK - Well extracted |
| `services/geo.py` | 58 | Good - Focused utilities |
| `utils/shift_time.py` | 56 | Good - Shared helpers |

---

## Summary

The Attendance feature is functionally complete but architecturally strained:

1. **Routes are too fat** - Business logic should be in services
2. **Duplicated code** - Enrichment logic in two places
3. **No clear layering** - Routes → Services → Repositories pattern not followed
4. **Two systems coexist** - Legacy and shift-based attendance
5. **Timezone complexity** - Scattered across multiple functions

The `ShiftAttendanceService` and `geo.py` show good extraction patterns that should be applied to the rest of the module.
