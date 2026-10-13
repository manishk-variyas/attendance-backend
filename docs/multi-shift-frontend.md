# Frontend Integration — Multi-Shift Attendance

What the frontend needs to do to support **multiple shifts per day** with
**per-shift check-in/out**.

**Backward compatible:** single-shift users see one entry; existing daily
endpoints keep their shape.

---

## 1. The new flow (multi-shift)

### 1.1 Today's shifts — `GET /api/attendance/today/shifts`
Returns **all** shifts relevant today (incl. an overnight shift from yesterday
that's still running, and an ad-hoc punch with no shift).
GET /server/api/location/233232 HTTP/1.1
Host: attendance.variyaslabs.com
Cookie: session_id=0GaJIoXFhzHyR8eLtkZOUCxcpVik9kH3RknYNeF2MUc;  device_id=0oSm-E6hWGan6PrjnBahJh9TABBy5VZvEnRSOzGlhug
Sec-Ch-Ua-Platform: "macOS"
X-User-Public-Ip: 104.28.192.188
Accept-Language: en-US,en;q=0.9
Sec-Ch-Ua: "Not A(Brand";v="99", "Chromium";v="154"
Content-Type: application/json
Sec-Ch-Ua-Mobile: ?0
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36
Accept: */*
Sec-Fetch-Site: same-origin
Sec-Fetch-Mode: cors
Sec-Fetch-Dest: empty
Referer: https://attendance.variyaslabs.com/dashboard
Accept-Encoding: gzip, deflate, br
Priority: u=1, i
Connection: keep-alive


```json
{
  "status": "in_progress",
  "date": "2026-10-10",
  "shifts": [
    {
      "shiftId": "82b0b688-...",          // pass to check-in/out; null for ad-hoc punch
      "shift_code": "GEN-IN",
      "shift_name": "Day Shift India",
      "date": "2026-10-10",
      "start_time": "2026-10-10T10:00:00+05:30",
      "end_time": "2026-10-10T18:00:00+05:30",
      "work_location_status": "OFFICE",
      "project_id": 8, "project_name": "VAPT TOOL",
      "status": "in_progress",            // per-shift status (see §2)
      "attendance": { ...punch or null... },
      "remainingHours": 3.5
    }
  ]
}
```

- Render **one card per shift** with a check-in / check-out button.
- `remainingHours`: hours left (while in progress) or hours until start
  (while `yet_to_start`); `0.0` when completed; `null` otherwise.

### 1.2 Check in / out per shift
```
POST /api/attendance/shifts/check-in
POST /api/attendance/shifts/check-out
```
Body (JSON):
```json
{ "shiftId": "82b0b688-...", "latitude": 28.4676, "longitude": 77.4813,
  "clientTimestamp": "2026-10-10T10:15:00+05:30" }
```
- `shiftId` **required**; `latitude`/`longitude` optional (no coords → `WFH`);
  `clientTimestamp` optional (server time if omitted).
- check-out also accepts `remarks`.
- Response: the **punch** object (same shape as `attendance` below).
- Idempotent: re-check-in / re-check-out returns the existing punch (no error).

### `attendance` (punch) object
```json
{
  "checkInTime": "2026-10-10T10:15:00+05:30", "checkInLat": 28.46, "checkInLng": 77.48,
  "checkInLocationName": "VLPL GREATER NOIDA, ...",
  "checkOutTime": null, "checkOutLat": null, "checkOutLng": null, "checkOutLocationName": null,
  "workLocationStatus": "OFFICE", "isLate": true, "totalHours": null,
  "shiftCode": "GEN-IN", "status": "in_progress", "updatedAt": "..."
}
```

---

## 2. Status values

**Overall (`status`)** — the card group / day state:
`no_shift` · `on_leave` · `yet_to_start` · `in_progress` · `completed`

**Per-shift (`shifts[].status`)**:
| Value | Meaning | Button |
|---|---|---|
| `yet_to_start` | shift not started | **Check in** |
| `in_progress` | checked in, not out | **Check out** |
| `overnight` | overnight shift, in progress | **Check out** |
| `shift_ended` | window ended, still checked in | **Check out** |
| `completed` | checked in & out | none |
| `missed` | window ended, never checked in | none |
| `on_leave` | approved leave that day | none |

`shiftId: null` + `status: in_progress/completed` = an **ad-hoc punch** (no shift).

---

## 3. Unchanged endpoints (no frontend change)
| Endpoint | Notes |
|---|---|
| `GET /api/attendance/today` | single active/upcoming shift (legacy) |
| `GET /api/attendance/my-attendance`, `/team-attendance` (`-v1`) | daily rows; **`totalHours` is now the SUM** of the day's shifts |
| `GET /api/shifts/roster` | per-shift records now include the punch (check-in/out, status) |
| `GET /api/shifts/range` | calendar (holidays) — unchanged |

---

## 4. Admin attendance — optional new fields
`GET /api/admin/attendance` rows now include:
`shiftId`, `expectedLocationId`, `actualLocationId`, `locationMismatch`
(expected office from the shift vs actual geofenced office).

---

## 5. Errors to handle
| Status | When |
|---|---|
| `400` | too early / shift ended / on leave for the shift date |
| `404` | shift not found / not yours / no check-in for check-out |
| `422` | missing `shiftId` or bad `latitude`/`longitude` type |
| `429` | rate limited |

---

## 6. Migration notes
- **Single-shift users:** `/attendance/today/shifts` returns **one** entry — you
  can render the same card, or keep using `/attendance/today`.
- **No payload changes** to existing endpoints.
- If you show "today", prefer `/attendance/today/shifts` (multi-shift aware).
- `totalHours` for multi-shift days = sum of the shifts (not the wall-clock span).

---

## 7. Suggested UI
1. Call `GET /attendance/today/shifts` on the dashboard.
2. Render a card per shift: code/name, time window, status chip, `remainingHours`.
3. Show **Check in** when `yet_to_start`; **Check out** when
   `in_progress`/`overnight`/`shift_ended`.
4. On click → `POST /attendance/shifts/check-in|out` with the entry's `shiftId`
   (+ geolocation if available), then refresh the list.
5. Keep the existing daily history/roster views — they already work.
