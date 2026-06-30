# Attendance App — Demo Guide

**Date:** Tuesday, June 30, 2026 — 11:00 AM IST

---

## 1. System URLs

| Service | URL |
|---|---|
| Backend API | `http://<server>:8000` |
| Keycloak Admin | `http://<server>:8080/auth` |
| Redmine | `http://<server>:3000` |

---

## 2. Login Credentials

### Admin Users

| Username | Password | Employee ID | Role | Redmine Role |
|---|---|---|---|---|
| `admin-vishal` | — | EMP-ADMIN-001 | System Administrator | Project Manager (all 5 projects) |

### Project Managers

| Username | Password | Employee ID | Redmine Role |
|---|---|---|---|
| `pm001` | — | EMP-PM-001 | Project Manager (all 5 projects) |
| `pm002` | — | EMP-PM-002 | Project Manager (all 5 projects) |

### Project Coordinators

| Username | Password | Employee ID | Redmine Role |
|---|---|---|---|
| `pc001` | — | EMP-PC-001 | Project Coordinator (all 5 projects) |
| `pc003` | — | EMP-PC-003 | Project Coordinator (all 5 projects) |
| `pc004` | — | EMP-PC-004 | Project Coordinator (all 5 projects) |
| `pc005` | — | EMP-PC-005 | Project Coordinator (all 5 projects) |
| `pc006` | — | EMP-PC-006 | Project Coordinator (all 5 projects) |

### Technical Resources / Developers

| Username | Password | Employee ID | Key Role |
|---|---|---|---|
| `tr001` | — | EMP-TR-001 | Technical Resource (all projects) |
| `tr002` | — | EMP-TR-002 | Technical Resource (all projects) |
| `tr003` | `tr003@123` | EMP-TR-003 | **Night shift demo** (all projects) |
| `himanshu` | — | EMP-SWE-101 | Technical Resource (all projects) |
| `rahulworks` | — | EMP-SWE01 | Technical Resource (all projects) |
| `manishkumar` | — | EMP-101 | Technical Resource (all projects) |
| `ritikyadav` | — | EMP-1010 | Developer (all projects) |

### Test Users

| Username | Password | Employee ID |
|---|---|---|
| `testerv01` | — | EMP-TV-001 |
| `testuser01` | — | EMP-TU-001 |

---

## 3. Shift Definitions

| Shift Code | Shift Name | Start | End | Timezone |
|---|---|---|---|---|
| `NIGHT` | Night Shift | 22:00 | 07:00 | Asia/Kolkata |
| `GEN-DEL` | Delhi General | 09:00 | 18:00 | Asia/Kolkata |
| `GEN-BLR` | Bangalore General | 10:00 | 19:00 | Asia/Kolkata |
| `GEN-MUM` | Mumbai General | 09:30 | 18:30 | Asia/Kolkata |
| `SG-STD` | Singapore Standard | 09:00 | 18:00 | Asia/Singapore |
| `US-EST` | US Eastern | 14:00 | 23:00 | America/New_York |

---

## 4. Office Locations (Geofences)

| Name | Latitude | Longitude | Radius | Category |
|---|---|---|---|---|
| **VARIYAS LABS OFFICE NOIDA** | 28.46788 | 77.48119 | 300m | Office |
| VARIYAS LABS PVT LIMITED | 28.46803 | 77.47997 | 300m | Office |
| DLF Techpark Noida | 28.45529 | 77.49520 | 300m | Customer_Site |

---

## 5. Company Settings

| Setting | Value |
|---|---|
| Company Name | Variyas Labs |
| Default Shift Start | 09:00 IST |
| Default Shift End | 18:00 IST |
| Grace Period | 2 minutes |

---

## 6. App Navigation & Key Features

### 8 Screens to Demo

| # | Screen | Key Feature to Show |
|---|---|---|
| 1 | **Dashboard** | Today's status, pending actions, remaining hours |
| 2 | **Schedule** | Daily shifts, project, timings per user |
| 3 | **Leaves** | Apply, view balances, approve/reject |
| 4 | **Attendance** | Check-in, check-out, history, forgot-day completion |
| 5 | **Employees** | Onboarding, designations, location/shift assignment |
| 6 | **Office Locations** | Geofenced offices, radius, category (Office/Customer_Site) |
| 7 | **Manage Shifts** | Shift definitions, daily shift allocations |
| 8 | **Settings** | Company defaults, grace time, timezone |

---

## 7. Full Workflow Demo Script

### Phase 1: Admin Setup (1 min — use admin-vishal)

1. **Office Locations** → Show 3 geofenced locations with radius
2. **Manage Shifts** → Show 6 shift definitions (NIGHT 22:00-07:00 as highlight)
3. **Settings** → Show company defaults (09:00-18:00, 2 min grace)
4. **Employees** → Show 19 employees with assigned locations

### Phase 2: Shift Allocation (2 min — use admin-vishal)

1. Go to Manage Shifts → Assign Shift
2. Pick TR003, date June 30, shift `NIGHT`, project Attendance_PWA
3. Confirm allocation is saved

### Phase 3: Employee Workflow (3 min — use TR003 / tr003@123)

**Scenario A: Early Check-in (Rejected)**
1. Login as TR003
2. Go to Attendance → Click **Start Shift**
3. At 11:00 AM IST, response:
   ```json
   {"detail": "Too early to check in. Your shift starts at 22:00 on 2026-06-30."}
   ```
4. Explain: guard prevents check-in more than 2 hours before shift

**Scenario B: Normal Check-in (Accepted after 20:00 IST)**
1. Fast-forward / simulate — after 20:00 IST
2. Click Start Shift → accepted
3. Response includes: `shiftCode: "NIGHT"`, `workLocationStatus`, `isLate: false`
4. If inside Noida geofence: `workLocationStatus: "OFFICE"`

**Scenario C: Night Shift Midnight Crossover**
1. TR003 checked in at 22:00 June 30, it's now 01:00 July 1
2. Open app → `/today` shows:
   ```
   attendanceDate: 2026-06-30
   shiftStartTime: 2026-06-30T22:00:00+05:30
   shiftEndTime: 2026-07-01T07:00:00+05:30     ← correctly next day
   remainingHours: 6.0
   ```
3. Click End Shift → check-out recorded, totalHours calculated

**Scenario D: Early Leave Detection**
1. TR003 checks in at 22:00, checks out at 23:00 (same night)
2. Response remarks: `"Early leave"` — detected because checkout is before shift end (07:00 next day)

### Phase 4: Forgot-Day Completion (1 min — use TR003)

1. `/pending` endpoint returns:
   - `pendingType: "forgot_end"` — checked in but forgot to check out
   - `pendingType: "forgot_start"` — shift assigned but no check-in
2. `/complete` endpoint — single API call to fix both check-in and check-out for a past day

### Phase 5: Idempotency & Guards (1 min)

1. **Double check-in** — click Start Shift twice → returns 200 with existing record (no error)
2. **Midnight rollover** — checked in yesterday but forgot checkout → blocked today's check-in until closed
3. **Check-out without check-in** — returns 404

---

## 8. Key Test Cases (Verbally Walkthrough)

| # | Test | Expected |
|---|---|---|
| 1 | Check-in too early (day shift at 6 AM) | `"Too early... shift starts at 09:00 on 2026-06-30"` |
| 2 | Check-in too early (night shift at 2 PM) | `"Too early... shift starts at 22:00 on 2026-06-30"` |
| 3 | Check-in within 2h window | ✅ Accepted |
| 4 | Check-in after grace → late | `isLate: true` |
| 5 | Double check-in | 200 idempotent (same record returned) |
| 6 | Check-out without check-in | 404 |
| 7 | Office geofence check | Inside: `OFFICE`, Outside: `WFH` |
| 8 | Night shift next-day checkout | Finds yesterday's record, no 404 |
| 9 | Yesterday open → block today check-in | `"Close yesterday's attendance before checking in today"` |
| 10 | Offline sync (clientTimestamp) | `isSynced: true`, `serverReceivedAt` set |

---

## 9. Redmine Integration

All 19 employees exist in Redmine with roles across 5 projects:

| Project | ID |
|---|---|
| DevOps_Noida | 2 |
| Kubernetes Gurgaon | 3 |
| AGENTIC AI | 4 |
| Attendance_PWA | 5 |
| PWA APP TESTING | 6 |

| Redmine Role | Assigned To |
|---|---|
| Project Manager | admin-vishal, pm001, pm002, rajansir |
| Project Coordinator | pc001, pc003, pc004, pc005, pc006 |
| Technical Resource | himanshu, manishkumar, rahulworks, testerv01, testuser01, tr001, tr002, tr003 |
| Developer | ritikyadav |

---

## 10. API Endpoints Reference

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| `POST` | `/auth/login` | Public | Login, get session cookie |
| `POST` | `/api/attendance/check-in` | Session | Start shift (lat, lng, optional clientTimestamp, date) |
| `POST` | `/api/attendance/check-out` | Session | End shift (lat, lng, remarks) |
| `POST` | `/api/attendance/complete` | Session | Fix forgot-day in one call |
| `GET` | `/api/attendance/pending` | Session | Check pending actions (forgot_start/forgot_end) |
| `GET` | `/api/attendance/today` | Session | Today's status + shiftStartTime/EndTime/remainingHours |
| `GET` | `/api/attendance/history` | Session | Past attendance records |
| `GET` | `/api/shifts/date/{date}` | Session | User's shift for a day |
| `POST` | `/api/shifts/assign` | Admin | Assign shift to employee |
| `GET/POST` | `/api/office-locations` | Admin | Manage geofence locations |
| `GET` | `/api/office-locations` | All (public) | View all offices |
| `POST` | `/api/employees` | Admin | Onboard new employee |
| `GET` | `/api/settings` | Admin | View/edit company settings |

---

## 11. Recent Bug Fixes (as of June 29, 2026)

| Fix | File | What Changed |
|---|---|---|
| Early check-in guard | `attendance/routes.py:196` | Rejects check-in >2h before shift start |
| Night shift end time | `attendance/routes.py:94` | `_shift_end_datetime()` helper — end_time < start_time → next day |
| Check-out fallback | `attendance/routes.py:283` | Searches yesterday's open attendance if today's not found |
| `/today` fallback | `attendance/routes.py:509` | Returns yesterday's open attendance if today has none |
| Idempotent check-in/out | `attendance/routes.py:152` | Duplicate requests return 200 (no 409) |
| `is_synced` / offline | `attendance/routes.py:80` | `clientTimestamp` handling, `isSynced` flag |
| `/complete` endpoint | `attendance/routes.py:358` | Single-call forgot-day fix |
| `/pending` endpoint | `attendance/routes.py:449` | Returns pendingType + shift info |
| Location category | `office_location.py` | VARCHAR CHECK constraint (Office/Customer_Site) |
| `require_active` fix | `auth/dependencies.py` | Missing employees not blocked, only deactivated |
| Self-only `/date/{date}` | `shifts/routes.py` | Returns only current user's shifts |
| Location GET public | `location/admin_routes.py` | GET endpoints open to all roles |
