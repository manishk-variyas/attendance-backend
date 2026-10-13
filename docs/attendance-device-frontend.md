# Frontend — Device Proof, OUT_OF_OFFICE & Remote Days

Backend changes that need frontend updates. **No request-payload changes** — the
server captures the device automatically from the `User-Agent` header.

---

## 1. Device fields (proof of which device checked in/out)

**Where they appear:**
- Per-shift punch (`POST /attendance/shifts/check-in|out` response + `GET /attendance/today/shifts` → `shifts[].attendance`):
  `checkInDevice`, `checkOutDevice` — e.g. `"Safari · iOS"`, `"Chrome · macOS"`, `null` if unknown.
- Admin attendance (`GET /api/admin/attendance`, `GET /{id}`, `POST`, `PUT`):
  same two fields.

**UI:**
- Show a device badge on the punch, e.g. `📱 Safari · iOS` vs `💻 Chrome · macOS`.
- Mobile = OS is `iOS`/`Android` (or contains "Mobile").
- `null` = checked in without a recognizable agent (API client, old app).

**No client change:** just send requests normally; the browser's `User-Agent` is
used. For the mobile app, ensure it sends a proper `User-Agent` string.

---

## 2. New work-location: `OUT_OF_OFFICE`

- `work_location_status` (and `workLocationStatus`) can now be **`OUT_OF_OFFICE`**
  — checked in far from the assigned office (previously mislabeled `WFH`).
- `WFH` now means **only** a WFH-designated shift (or home).
- Add `OUT_OF_OFFICE` to:
  - shift form location dropdowns (`GET /shifts/work-locations` already lists it),
  - attendance list filters,
  - status chips (suggested label: **"Out of office"**, distinct from "WFH").

---

## 3. New `remote_days` + changed `office_days`

`GET /api/analytics/attendance/me` and `/member` → `attendance`:
- **`remote_days`** (new) — present days with `REMOTE_*` / `OUT_OF_OFFICE`.
- **`office_days`** — now `present − wfh − remote` (was `present − wfh`).
- Team member rows (`/attendance/team` → `members[]`) and `totals` also carry
  `remote_days`.

**UI:** add a "Remote" stat next to WFH; `office_days` needs no client math
change if you read it from the API (update it only if you recompute it locally
as `present − wfh`).

---

## 4. Admin attendance — location link fields (already shipped, for reference)
- `shiftId`, `expectedLocationId` (live from the shift), `actualLocationId`
  (geofenced), `locationMismatch` (bool). Show a warning when `true`.

---

## 5. Nothing else changes
- Request shapes, `present`/`leave`/`holiday`/`absent` statuses, and all other
  endpoints are unchanged.
- Single-office users see no difference unless they punch in far away.
