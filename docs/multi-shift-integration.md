# Multi-Shift Attendance — Integration Guide

**Audience:** frontend developer
**Status:** backend implemented; frontend integration pending
**Backward compatible:** single-shift users keep working; no payload changes to
existing endpoints.

---

## 0. TL;DR — what to build
1. On the dashboard, call **`GET /api/attendance/today/shifts`** and render **one
   card per shift** (code, window, status, remaining hours).
2. Buttons call **`POST /api/attendance/shifts/check-in`** / **`…/check-out`**
   with the card's `shiftId`.
3. Leave the existing daily list / roster / calendar views as-is (they already
   return per-shift punch data; only `totalHours` semantics improved).

---

## 1. Data model in one line
- A day can have **N shifts**; each shift has **its own check-in/out**.
- `attendance` = **daily rollup** (present/hours/late) — what the lists show.
- `attendance_punches` = **one punch per shift** — the source of truth.

---

## 2. Endpoints overview

| Endpoint | Purpose | Changed? |
|---|---|---|
| `GET /api/attendance/today/shifts` | **all shifts today** (multi-shift) | ✅ use this |
| `POST /api/attendance/shifts/check-in` | check in **one shift** | ✅ |
| `POST /api/attendance/shifts/check-out` | check out **one shift** | ✅ |
| `GET /api/attendance/today` | single active/upcoming shift | unchanged |
| `GET /api/attendance/my-attendance` | daily history | unchanged (hours=sum) |
| `GET /api/attendance/team-attendance` | team daily history | unchanged (hours=sum) |
| `GET /api/shifts/roster` | per-shift schedule + punch | unchanged shape, real punch data |
| `GET /api/shifts/my-shifts-v1` | per-shift history | unchanged shape, real punch data |
| `GET /api/shifts/team-shifts-v1` | team per-shift history | unchanged shape |
| `GET /api/shifts/range` | calendar (shifts/leave/holidays) | unchanged |
| `GET/POST/PUT/DELETE /api/admin/attendance` | admin CRUD | new fields |

Auth: session cookie. Role: attendance endpoints need an active employee;
`today/shifts` + per-shift check-in/out = any authenticated active user.

---

## 3. `GET /api/attendance/today/shifts`

Returns every shift relevant today — including an **overnight shift from
yesterday** that is still running, and an **ad-hoc punch** with no shift.

**Response 200**
```json
{
  "status": "in_progress",
  "date": "2026-10-10",
  "shifts": [
    {
      "shiftId": "82b0b688-4840-4ace-abf6-7f945777af71",
      "shift_code": "GEN-IN",
      "shift_name": "Day Shift India",
      "date": "2026-10-10",
      "start_time": "2026-10-10T10:00:00+05:30",
      "end_time": "2026-10-10T18:00:00+05:30",
      "work_location_status": "OFFICE",
      "project_id": 8,
      "project_name": "VAPT TOOL",
      "status": "in_progress",
      "attendance": {
        "checkInTime": "2026-10-10T10:15:00+05:30",
        "checkInLat": 28.46, "checkInLng": 77.48,
        "checkInLocationName": "VLPL GREATER NOIDA, Knowledge Park III, …",
        "checkOutTime": null, "checkOutLat": null, "checkOutLng": null, "checkOutLocationName": null,
        "workLocationStatus": "OFFICE",
        "isLate": true,
        "totalHours": null,
        "officeName": null,
        "shiftCode": "GEN-IN",
        "status": "in_progress",
        "remarks": null,
        "updatedAt": "2026-10-10T10:15:00+05:30"
      },
      "remainingHours": 3.5
    }
  ]
}
```

### Field reference
| Field | Type | Notes |
|---|---|---|
| `status` | string | **overall** day state (§4) |
| `date` | string | IST date |
| `shifts[].shiftId` | string \| null | pass to check-in/out; `null` = ad-hoc punch |
| `shifts[].shift_code` / `shift_name` | string \| null | |
| `shifts[].date` | string | shift's date (may be yesterday for overnight) |
| `shifts[].start_time` / `end_time` | string \| null | ISO, shift timezone |
| `shifts[].work_location_status` | string | `OFFICE` / `WFH` / … |
| `shifts[].project_id` / `project_name` | int / string | |
| `shifts[].status` | string | **per-shift** status (§4) |
| `shifts[].attendance` | object \| null | the punch (shape in §3.1) |
| `shifts[].remainingHours` | float \| null | hours left (in progress) / until start (`yet_to_start`); `0.0` completed |

### 3.1 `attendance` (punch) object
| Field | Type |
|---|---|
| `checkInTime`, `checkOutTime` | ISO \| null |
| `checkInLat`, `checkInLng`, `checkOutLat`, `checkOutLng` | float \| null |
| `checkInLocationName`, `checkOutLocationName` | string \| null |
| `workLocationStatus` | string |
| `isLate` | bool |
| `totalHours` | float \| null |
| `officeName` | string \| null |
| `shiftCode` | string \| null |
| `status` | string (`in_progress` / `completed` / `absent`) |
| `remarks` | string \| null |
| `updatedAt` | ISO \| null |

---

## 4. Status values

**Overall `status`** (the group / day):
`no_shift` · `on_leave` · `yet_to_start` · `in_progress` · `completed`

**Per-shift `shifts[].status`**:
| Value | Meaning | Button to show |
|---|---|---|
| `yet_to_start` | not started yet | **Check in** |
| `in_progress` | checked in, not out | **Check out** |
| `overnight` | overnight shift, in progress | **Check out** |
| `shift_ended` | window ended, still checked in | **Check out** |
| `completed` | checked in & out | none |
| `missed` | window ended, never checked in | none |
| `on_leave` | approved leave that day | none |

- `shiftId: null` with `in_progress`/`completed` = an **ad-hoc punch** (no shift).

---

## 5. `POST /api/attendance/shifts/check-in`

**Request (JSON)**
```json
{
  "shiftId": "82b0b688-4840-4ace-abf6-7f945777af71",
  "latitude": 28.4676,
  "longitude": 77.4813,
  "clientTimestamp": "2026-10-10T10:15:00+05:30"
}
```
| Field | Required | Notes |
|---|---|---|
| `shiftId` | ✅ | the shift to check into |
| `latitude`, `longitude` | no | omit → server marks `WFH` |
| `clientTimestamp` | no | ISO; server time if omitted (clamped to ±24h) |

**Response 201** — the punch object (§3.1).
- **Idempotent:** re-check-in for the same shift returns the existing punch.

---

## 6. `POST /api/attendance/shifts/check-out`

Same body as check-in **+ optional `remarks`** (max 100).

**Response 200** — the punch object, now with `checkOutTime`, `totalHours`,
`status: "completed"`.
- **Idempotent:** re-check-out returns the existing punch.

---

## 7. Errors (both check-in/out)
| Status | Detail |
|---|---|
| `400` | `Too early to check in. Shift starts at HH:MM.` |
| `400` | `Shift has already ended. Cannot check in.` |
| `400` | `You are on leave for this shift date. Cannot check in.` |
| `404` | `Shift not found` (unknown / not yours) |
| `404` | `No check-in found for this shift` (check-out without check-in) |
| `422` | missing `shiftId` / bad `latitude`/`longitude` type |
| `429` | rate limited |

---

## 8. Unchanged endpoints (keep existing UI)

- **`GET /api/attendance/today`** — single active/upcoming shift.
  `{ status, shift, attendance, remainingHours }`.
- **`GET /api/attendance/my-attendance` / `/team-attendance`** (`-v1`) — daily
  rows. **`totalHours` is now the SUM of the day's shifts** (multi-shift days).
- **`GET /api/shifts/roster`** — per-shift records; now carry the **real punch**
  (`checkInTime`, `checkOutTime`, `status`, `totalHours`, location fields).
- **`GET /api/shifts/my-shifts-v1` / `team-shifts-v1`** — per-shift history with
  the real punch.
- **`GET /api/shifts/range`** — calendar timeline (shift/leave/holiday).

---

## 9. Admin attendance — new fields
`GET /api/admin/attendance` (and `GET /{id}`, `POST`, `PUT`) responses include:

| Field | Meaning |
|---|---|
| `shiftId` | linked roster shift |
| `expectedLocationId` | office from the shift (read live) |
| `actualLocationId` | geofenced office at punch (null if WFH) |
| `locationMismatch` | `expected != actual` |

`POST /api/admin/attendance` body unchanged; `PUT` validates `shift_code` and
re-links `shiftId` automatically.

---

## 10. Backend behaviour notes
- **Hours:** `totalHours` on a multi-shift day = **sum** of shift hours (not the
  wall-clock span).
- **Day boundary:** IST (unchanged for now; shift-tz is the future rule).
- **Overnight:** a shift is attributed to its **start date**; check-out may be
  the next day.
- **Rollup:** every punch write recomputes the daily `attendance` row in the same
  transaction.

---

## 11. Suggested UI flow
1. Dashboard → `GET /attendance/today/shifts`.
2. For each shift render a card: code/name, window, status chip, `remainingHours`.
3. Show **Check in** when `yet_to_start`; **Check out** when
   `in_progress` / `overnight` / `shift_ended`.
4. On click → `POST /attendance/shifts/check-in|out` with the card's `shiftId`
   (+ geolocation if permitted), then refresh the list.
5. Keep existing daily-history / roster / calendar screens (unchanged contract).
6. Single-shift users: `/today/shifts` returns **one** entry — you may render the
   same card, or keep using `/attendance/today`.

---

## 12. Quick examples (curl)

```bash
# today's shifts
curl -s -b cookies.txt "$BASE/api/attendance/today/shifts"

# check in (office)
curl -s -b cookies.txt -X POST "$BASE/api/attendance/shifts/check-in" \
  -H 'Content-Type: application/json' \
  -d '{"shiftId":"82b0b688-…","latitude":28.4676,"longitude":77.4813}'

# check out
curl -s -b cookies.txt -X POST "$BASE/api/attendance/shifts/check-out" \
  -H 'Content-Type: application/json' \
  -d '{"shiftId":"82b0b688-…","latitude":28.4676,"longitude":77.4813,"remarks":"done"}'
```
