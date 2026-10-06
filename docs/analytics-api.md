# Analytics API Reference

Read-only dashboard summaries for attendance, tickets, leaves, and WFH.
Base URL prefix: `/api`

All eight endpoints accept the same date params and return chart-ready shapes
(arrays, not just totals).

---

## 0. Common query parameters & rules (all endpoints)

| Param | Type | Required | Default / limits |
|---|---|---|---|
| `from_date` | string | no | default: first day of current month. `YYYY-MM-DD`, else `400 Invalid date format. Use YYYY-MM-DD.` |
| `to_date` | string | no | default: today. `YYYY-MM-DD` |
| `project_id` | int | no | team endpoints only — narrow to one project |
| `search` | string | no | team endpoints only — case-insensitive substring match on the member's full name; max 100 chars. Omit to return every member. |

Rules: `from_date <= to_date` (else `400`), max range 366 days (else `400`).
Rate limit: 30 requests/minute. Auth: session cookie (same as all APIs).

### Searching & filtering (team endpoints)

All six team endpoints — `/attendance/team`, `/attendance/punctuality/team`,
`/attendance/absenteeism/team`, `/tickets/team`, `/leaves/team`, `/wfh/team` —
support the same two filters:

- **`search=<name>`** — substring match on `firstname + " " + lastname`
  (`ILIKE '%term%'`, case-insensitive). e.g. `search=Himanshu` returns
  `Himanshu Pokhariya` and `Himanshu Singh`. No match ⇒ `member_count: 0`.
- **`project_id=<id>`** — narrows the team to one project (PM/PC must belong to
  it, else `403`; unknown id ⇒ `404 Project not found`).

Filters combine, and every per-member list, total, and (where present) the
`monthly`/`trend` aggregates are recomputed over the filtered scope.

### Common error responses

| Status | Body | Meaning |
|---|---|---|
| `401` | `{ "detail": "Invalid or expired session" }` | not logged in |
| `403` | `{ "detail": "Access denied" }` | role not allowed (team endpoints: Admin/PM/PC only) |
| `403` | `{ "detail": "You are not a member of this project" }` | PM/PC filtering by a project they don't belong to |
| `404` | `{ "detail": "User not found in Redmine" }` | session email not synced to Redmine |
| `429` | rate-limit payload | over 30 requests/minute |

### Attendance counting rules

- **Working days come from the roster**: a day is a working day for a member
  **only if they have a shift** that day (a `shifts` row). Weekdays, weekends,
  and holidays are all treated the same way — the assigned schedule decides.
  No-shift members have 0 working days.
- `present_days` = rows with `status = present` (includes OFFICE / WFH / remote).
- `leave_days` = rows with `work_location_status = LEAVE` (or leave status).
- `wfh_days` = present rows with `work_location_status = WFH`; `office_days = present − wfh`.
- `absent_days = max(working_days − present_days − leave_days, 0)` — a scheduled
  day with no punch counts as absent.
- `attendance_pct = present_days / working_days × 100` (0 when no shifts).
- Team scope includes **active employees only** (`employee_master.is_active`).

### Per-member drill-downs (shared rules)
`/attendance/member`, `/tickets/member`, `/leaves/member`, `/wfh/member` share one
access model and one target key:

- Provide `redmine_user_id` (preferred — the value on the `/…/team` rows) or
  `user_email` (deprecated fallback).
- **Self:** any authenticated role may request their own id/email.
- **Lead:** Admin / Project Manager / Project Coordinator may request any target
  in their team scope. A non-lead asking for someone else gets `403`.
- Out-of-scope/unknown target or an account with no employee login ⇒ `404`.
- Team rows now carry **`has_login: bool`** — disable the drill-down when `false`
  (e.g. test/vendor Redmine accounts have no attendance to analyse).

---

## 1. My attendance summary

```
GET /api/analytics/attendance/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: Admin, Project Manager, Project Coordinator, Technical Resource.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "working_days": 9,
  "recorded_days": 9,
  "present_days": 9,
  "absent_days": 0,
  "leave_days": 0,
  "wfh_days": 0,
  "office_days": 9,
  "late_days": 3,
  "total_hours": 90.4,
  "avg_hours_per_day": 10.04,
  "attendance_pct": 100.0,
  "by_status": { "present": 9 },
  "daily": [
    {
      "date": "2026-09-01",
      "status": "present",
      "work_location": "OFFICE",
      "check_in": "2026-09-01T04:50:05.884676+00:00",
      "check_out": "2026-09-01T12:29:42.787463+00:00",
      "hours": 7.66,
      "is_late": true
    }
  ]
}
```

Field notes: `daily` has one entry per recorded row (sorted by date).
`check_in` / `check_out` / `hours` are `null` when not punched.
`by_status` maps raw status value → count.

---

## 1b. One employee's attendance drill-down

```
GET /api/analytics/attendance/member?redmine_user_id=20&from_date=2026-09-01&to_date=2026-09-11&project_id=33
```
Access — two server-resolved paths (never trust a client "self" flag):
- **Self:** any authenticated role may request **their own** `redmine_user_id`
  (or their own email). The viewer is computed from the session, not the client.
- **Lead:** Admin / Project Manager / Project Coordinator may request a target
  **inside their team scope** (same project-membership rules as `/attendance/team`).

`redmine_user_id` preferred (stable, unique — matches the value returned by
`/attendance/team`); `user_email` is a **deprecated fallback**. `project_id`
optional (restricts the lead lookup).

Errors: `400` when neither identifier is supplied; `403` when a non-lead asks for
someone other than themselves; `404` when the target is out of scope **or** the
account has no linked login (test/vendor/service accounts). The user's `/me`
endpoints remain the lightweight self-dashboard.

Returns the employee's attendance summary, punctuality and absenteeism in a
single call (same shapes as sections 1 / 2a / 2c):

```json
{
  "user": { "redmine_user_id": 20, "name": "Abhilash Malik", "email": "abhilash@variyaslabs.com" },
  "attendance":  { "working_days": 9, "present_days": 7, "absent_days": 2, "late_days": 4, "attendance_pct": 77.8, "total_hours": 80.98, "daily": [] },
  "punctuality": { "late_days": 4, "total_checkins": 7, "avg_check_in": "10:25", "by_weekday": {}, "trend": [] },
  "absenteeism": { "absent_days": 2, "longest_streak": 0, "monday_absences": 0, "friday_absences": 1, "streaks": [] }
}
```

Errors: `400` when neither `redmine_user_id` nor `user_email` is provided;
`403` when a non-lead requests someone other than themselves; `404` target not
in scope **or** account has no attendance data (test/vendor/service accounts
with no employee login). To build a picker, use the `/attendance/team` list
(already returns `redmine_user_id`, name, email) or the employee suggest
endpoint.

---

## 2. Team attendance

```
GET /api/analytics/attendance/team?from_date=2026-09-01&to_date=2026-09-11&project_id=33&sort_by=late_days&sort_dir=desc&group_by=week&hide_empty=true
```
Roles: Admin, Project Manager, Project Coordinator only.
`project_id` optional — narrows to members of that project (PM/PC must belong
to it, else `403`; unknown id → `404 Project not found`).

Extra params: `sort_by` (name, attendance_pct, present_days, absent_days,
late_days, total_hours; default `name`), `sort_dir` (`asc`/`desc`, default
`asc`), `group_by` (`day`/`week`/`month` buckets for the grid, default `day`),
`hide_empty` (default `false`; `true` drops members with no rows),
`search` (full-name substring filter, see §0).

Team scope: Admin → project members (or all active employees when no
`project_id`); PM/PC → members sharing a project with the viewer (+ self).

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "working_days": 9,
  "member_count": 28,
  "members": [
    {
      "redmine_user_id": 20,
      "name": "Abhilash Malik",
      "email": "abhilash@variyaslabs.com",
      "present_days": 7,
      "absent_days": 2,
      "leave_days": 0,
      "wfh_days": 6,
      "late_days": 4,
      "total_hours": 68.16,
      "attendance_pct": 77.8
    }
  ],
  "totals": {
    "present_days": 0,
    "absent_days": 0,
    "leave_days": 0,
    "wfh_days": 0,
    "late_days": 0,
    "total_hours": 0.0
  },
  "daily": [
    { "date": "2026-09-01", "present": 5, "absent": 23, "leave": 0, "wfh": 2 }
  ]
}
```

Field notes: members are sorted per `sort_by`/`sort_dir`. With
`hide_empty=true` the totals and absentee counts cover shown members only.
`daily` buckets follow `group_by` (`date` = day, week-Monday, or month-start).
With `group_by=week|month`, `absent` = members × workdays in bucket − present
− leave. `group_by` is echoed in the response.

---

## 2a. My punctuality

```
GET /api/analytics/attendance/punctuality/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: all four. Times in IST (`HH:MM`).

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "late_days": 3,
  "total_checkins": 9,
  "avg_check_in": "10:44",
  "earliest_check_in": { "date": "2026-09-01", "time": "10:20" },
  "latest_check_in": { "date": "2026-09-11", "time": "10:01" },
  "by_weekday": { "Mon": 0, "Tue": 1, "Wed": 1, "Thu": 0, "Fri": 1, "Sat": 0, "Sun": 0 },
  "trend": [{ "date": "2026-09-01", "late": 1, "check_in": "10:20" }]
}
```

---

## 2b. Team punctuality

```
GET /api/analytics/attendance/punctuality/team?from_date=2026-09-01&to_date=2026-09-11&late_min=2
```
Roles: Admin/PM/PC. `late_min` (1–31, default 3) marks habitual latecomers.
Also supports `project_id` and `search` (§0); `habitual_count`, `by_weekday`
and `trend` are recomputed over the filtered scope.

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "late_threshold": 2,
  "member_count": 28,
  "habitual_count": 5,
  "ranking": [
    { "redmine_user_id": 116, "name": "udit", "email": "udit@variyaslabs.com",
      "late_days": 7, "total_checkins": 7, "avg_check_in": "11:48", "habitual": true }
  ],
  "by_weekday": { "Mon": 0, "Tue": 5, "Wed": 4, "Thu": 3, "Fri": 6, "Sat": 0, "Sun": 0 },
  "trend": [{ "date": "2026-09-01", "late": 4 }]
}
```
`ranking` sorted by `late_days` desc.

---

## 2c. My absenteeism

```
GET /api/analytics/attendance/absenteeism/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: all four. Streaks = consecutive absent working days (min length 2).

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "absent_days": 0,
  "longest_streak": 0,
  "streaks": [{ "start": "2026-09-08", "end": "2026-09-10", "length": 3 }],
  "by_weekday": { "Mon": 1, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 2, "Sat": 0, "Sun": 0 },
  "monday_absences": 1,
  "friday_absences": 2
}
```

---

## 2d. Team absenteeism

```
GET /api/analytics/attendance/absenteeism/team?from_date=2026-09-01&to_date=2026-09-11&streak_min=3
```
Roles: Admin/PM/PC. `streak_min` (2–31, default 3) marks red-flag streaks.
Also supports `project_id` and `search` (§0); `red_flags`, `zero_attendance`
and `totals` are recomputed over the filtered scope.

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "streak_threshold": 3,
  "member_count": 28,
  "members": [
    { "redmine_user_id": 141, "name": "ac@3rinfotech.com -", "email": "ac@3rinfotech.com",
      "absent_days": 9, "longest_streak": 9, "monday_absences": 1, "friday_absences": 2 }
  ],
  "red_flags": [
    { "name": "ac@3rinfotech.com -", "email": "ac@3rinfotech.com",
      "start": "2026-09-01", "end": "2026-09-11", "length": 9 }
  ],
  "zero_attendance": ["demouser -"],
  "totals": { "absent_days": 0, "members_with_streaks": 0 }
}
```
`members` sorted by `absent_days` desc; `red_flags` by streak length desc;
`zero_attendance` lists members with no rows at all in range.

---

## 3. My ticket analytics

```
GET /api/analytics/tickets/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: Admin, Project Manager, Project Coordinator, Technical Resource.

Scope: `open_now` / `overdue_now` are current state (all time) of tickets
assigned to me; breakdowns and trend cover tickets assigned to me created
in `[from_date, to_date]`.

### `scope` — date filtering for the header counts

`GET /api/analytics/tickets/me?from_date=2026-08-01&to_date=2026-08-31&scope=range`

- `scope=current` (default): header counts (`total`, `assigned_to_me`,
  `created_by_me`, `open`, `pending`, `closed`, `overdue`, `open_now`,
  `overdue_now`) describe **all my tickets as of now** — the range only
  drives `created/closed_in_range`, breakdowns, and `trend`.
- `scope=range`: the header describes the **created-in-range cohort with
  current statuses** ("of last month's tickets, what's still open").
  `open_now`/`overdue_now` then equal the cohort's `open`/`overdue`.
  Statuses are always *as of now* — there is no status-history table.

Status buckets: `closed` = Redmine `is_closed`; `pending` = `Resolved`;
`open` = other non-closed. All date boundaries use IST explicitly.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "open_now": 42,
  "overdue_now": 8,
  "created_in_range": 35,
  "closed_in_range": 3,
  "authored_in_range": 31,
  "avg_resolution_days": 0.0,
  "by_status": [{ "id": 1, "name": "New", "count": 23 }],
  "by_priority": [{ "id": 1, "name": "Low P3", "count": 13 }],
  "by_tracker": [{ "id": 2, "name": "Feature", "count": 20 }],
  "trend": [
    { "date": "2026-09-01", "created": 0, "closed": 0 },
    { "date": "2026-09-02", "created": 14, "closed": 3 }
  ]
}
```

Field notes:
- `overdue_now` = open tickets with `due_date < today`.
- `authored_in_range` = tickets I created in range (regardless of assignee).
- `avg_resolution_days` = mean `closed_on − created_on` in days for closed
  tickets in range; `null` when none closed. Rounded to 1 decimal.
- `by_status` / `by_priority` / `by_tracker` sorted by count desc.
- `trend` covers every calendar date in range (`created` = assigned tickets
  created that day, `closed` = assigned tickets closed that day).

---

## 4. Team ticket analytics

```
GET /api/analytics/tickets/team?from_date=2026-09-01&to_date=2026-09-11&project_id=33&search=Himanshu
```
Roles: Admin, Project Manager, Project Coordinator only.
`project_id` optional — narrows to that project (PM/PC must belong to it).
`search` optional — full-name substring filter (§0).
`scope` optional — `current` (default) keeps `open_now`/`overdue_now` as the
all-time state of assigned tickets; `range` restricts those (and the per-member
and per-project lists) to tickets **created in** `[from_date, to_date]`.
`created_in_range`/`closed_in_range` are range-scoped in both modes.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "member_count": 12,
  "members": [
    {
      "redmine_user_id": 56,
      "name": "Manish Mishra",
      "email": "manishk@variyaslabs.com",
      "open_now": 42,
      "overdue_now": 8,
      "created_in_range": 35,
      "closed_in_range": 3
    }
  ],
  "by_project": [
    { "project_id": 33, "project_name": "HEPC", "total": 7, "open": 7, "closed": 0 }
  ],
  "totals": {
    "open_now": 7,
    "overdue_now": 0,
    "created_in_range": 7,
    "closed_in_range": 0
  }
}
```

Field notes: `members` lists only team members who have at least one
assigned ticket matching the filter (sorted by name). `by_project` counts
team-assigned issues per project, sorted by total desc. `totals` are sums
over `members`.

---

## 4b. One employee's ticket analytics

```
GET /api/analytics/tickets/member?redmine_user_id=72&from_date=2026-09-01&to_date=2026-09-29
```
Self (any role) or Admin/PM/PC in scope — same access model as §1b. Returns the
same `tickets` payload as §3 plus the `user` identity:

```json
{
  "user": { "redmine_user_id": 72, "name": "Himanshu Pokhariya", "email": "himanshu@variyaslabs.com" },
  "tickets": { "open_now": 4, "overdue_now": 1, "created_in_range": 2, "closed_in_range": 1,
               "authored_in_range": 1, "avg_resolution_days": 2.5,
               "by_status": [], "by_priority": [], "by_tracker": [], "trend": [] }
}
```

Errors: `400` no identifier · `403` non-lead for another user · `404` out of
scope / no linked login.

---

## Leaves & WFH counting rules

- Counts cover requests **overlapping** `[from_date, to_date]` (the time off
  falls in the period), not only requests created in it.
- Leave status buckets: `approved` = approved + emergency; `pending` =
  pending + cancellation_requested; `rejected` = rejected +
  cancellation_rejected; `cancelled` = cancelled. `by_status` keeps raw values.
- `approved_days` counts the leave's overlap with the member's **assigned shift
  days** (the roster) — same working-day rule as attendance. Leave on a day with
  no shift counts 0.
- `trend` points are keyed by request `start_date` (`applied` = requests
  starting that day, `approved` = approved ones starting that day).
- `monthly` = last 6 calendar months ending with `to_date`'s month
  (`month: "YYYY-MM"`), one call for the full chart.
- No leave **balances** here — use the existing `/api/leaves/*balance*` endpoints.

---

## 5. My leave activity

```
GET /api/analytics/leaves/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: Admin, Project Manager, Project Coordinator, Technical Resource.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "applied": 2,
  "approved": 0,
  "pending": 0,
  "rejected": 1,
  "cancelled": 1,
  "approved_days": 0,
  "by_type": [{ "type": "EL", "requests": 1, "days": 0 }],
  "by_status": [{ "status": "rejected", "count": 1 }],
  "trend": [{ "date": "2026-09-01", "applied": 0, "approved": 0 }],
  "monthly": [{ "month": "2026-09", "applied": 2, "approved": 0, "approved_days": 0 }]
}
```

Field notes: `by_type` sorted by type; `days` counts approved working-day
overlap only. `by_status` sorted by count desc. `trend` covers every calendar
date.

---

## 6. Team leave activity

```
GET /api/analytics/leaves/team?from_date=2026-09-01&to_date=2026-09-11&project_id=33
```
Roles: Admin, Project Manager, Project Coordinator only.
`project_id` optional. Same team scope as the other team endpoints.
`search` optional — full-name substring filter (§0). The `monthly` series is
recomputed over the filtered scope.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "member_count": 28,
  "members": [
    {
      "redmine_user_id": 20,
      "name": "Abhilash Malik",
      "email": "abhilash@variyaslabs.com",
      "applied": 0,
      "approved": 0,
      "pending": 0,
      "approved_days": 0
    }
  ],
  "totals": { "applied": 0, "approved": 0, "pending": 0, "approved_days": 0 },
  "monthly": [{ "month": "2026-09", "applied": 0, "approved": 0, "approved_days": 0 }]
}
```

---

## 6b. One employee's leave activity

```
GET /api/analytics/leaves/member?redmine_user_id=72&from_date=2026-09-01&to_date=2026-09-29
```
Self (any role) or Admin/PM/PC in scope. Returns the same `leaves` payload as §5
plus the `user` identity:

```json
{
  "user": { "redmine_user_id": 72, "name": "Himanshu Pokhariya", "email": "himanshu@variyaslabs.com" },
  "leaves": { "applied": 4, "approved": 2, "pending": 1, "rejected": 1, "cancelled": 0,
              "approved_days": 2, "by_type": [], "by_status": [], "trend": [], "monthly": [] }
}
```

Errors: `400` no identifier · `403` non-lead for another user · `404` out of
scope / no linked login.

---

## 7. My WFH activity

```
GET /api/analytics/wfh/me?from_date=2026-09-01&to_date=2026-09-11
```
Roles: Admin, Project Manager, Project Coordinator, Technical Resource.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "total": 1,
  "pending": 0,
  "approved": 0,
  "rejected": 1,
  "approved_days": 0,
  "by_status": [{ "status": "rejected", "count": 1 }],
  "trend": [{ "date": "2026-09-01", "applied": 1, "approved": 0 }],
  "monthly": [{ "month": "2026-09", "total": 1, "approved": 0, "approved_days": 0 }]
}
```

---

## 8. Team WFH activity

```
GET /api/analytics/wfh/team?from_date=2026-09-01&to_date=2026-09-11&project_id=33
```
Roles: Admin, Project Manager, Project Coordinator only.
`project_id` optional. Same team scope as the other team endpoints.
`search` optional — full-name substring filter (§0). The `monthly` series is
recomputed over the filtered scope.

### Success response `200`

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-11",
  "member_count": 28,
  "members": [
    {
      "redmine_user_id": 20,
      "name": "Abhilash Malik",
      "email": "abhilash@variyaslabs.com",
      "total": 0,
      "approved": 0,
      "pending": 0,
      "approved_days": 0
    }
  ],
  "totals": { "total": 0, "approved": 0, "pending": 0, "approved_days": 0 },
  "monthly": [{ "month": "2026-09", "total": 0, "approved": 0, "approved_days": 0 }]
}
```

---

## 8b. One employee's WFH activity

```
GET /api/analytics/wfh/member?redmine_user_id=72&from_date=2026-09-01&to_date=2026-09-29
```
Self (any role) or Admin/PM/PC in scope. Returns the same `wfh` payload as §7
plus the `user` identity:

```json
{
  "user": { "redmine_user_id": 72, "name": "Himanshu Pokhariya", "email": "himanshu@variyaslabs.com" },
  "wfh": { "total": 3, "pending": 1, "approved": 2, "rejected": 0, "approved_days": 2,
           "by_status": [], "trend": [], "monthly": [] }
}
```

Errors: `400` no identifier · `403` non-lead for another user · `404` out of
scope / no linked login.

---

## 9. Who's out today

```
GET /api/analytics/team/out-today?project_id=33
```
Roles: Admin, Project Manager, Project Coordinator only.
`project_id` optional. Same team scope as the other team endpoints.
No date params — always today (server date).

### Success response `200`

```json
{
  "date": "2026-09-12",
  "on_leave": [
    {
      "name": "Abhilash Malik",
      "email": "abhilash@variyaslabs.com",
      "leave_type": "EL",
      "start_date": "2026-09-12",
      "end_date": "2026-09-13"
    }
  ],
  "on_wfh": [
    {
      "name": "Manish Mishra",
      "email": "manishk@variyaslabs.com",
      "start_date": "2026-09-12",
      "end_date": "2026-09-12"
    }
  ],
  "counts": { "on_leave": 1, "on_wfh": 1 }
}
```

Field notes: only **approved** leaves/WFH overlapping today, sorted by name.
