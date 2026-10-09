# Analytics — Per-Employee (Member) Detail API

Four endpoints return **one employee's** analytics. Same access model and params
for all four.

---

## Shared contract

| Item | Value |
|---|---|
| Auth | session cookie (`get_current_user`) |
| Role | any member role (self) · Admin / PM / PC (others in team scope) |
| Rate limit | 30 requests / minute |
| Base path | `/api/analytics` |

### Query params (all four)

| Param | Type | Required | Notes |
|---|---|---|---|
| `redmine_user_id` | int | preferred | target employee's Redmine user id (the value on team rows) |
| `user_email` | string | deprecated | fallback if no id |
| `from_date` | string | no | `YYYY-MM-DD`, default = first day of current month |
| `to_date` | string | no | `YYYY-MM-DD`, default = today |
| `project_id` | int | no | restrict the target lookup to one project |

`tickets/member` also has `scope` (`current` \| `range`, default `current`).

### Access rules
- **Self:** any authenticated role may request their own id/email.
- **Lead:** Admin / PM / PC may request any target **inside their team scope**.
- A non-lead asking for someone else → `403`.

### Errors
| Status | Meaning |
|---|---|
| `400` | no `redmine_user_id`/`user_email`; bad date; range > 366 days |
| `403` | not allowed for this target |
| `404` | target not found / out of scope / not linked to an employee login |

### `user` object (all responses)
```json
{ "redmine_user_id": 32, "name": "Vishal Singh", "email": "vishal.singh@variyaslabs.com" }
```

---

## 1. Attendance — `GET /api/analytics/attendance/member`

```json
{
  "user": { ... },
  "attendance": { AttendanceSummary },
  "punctuality": { PunctualityMe },
  "absenteeism": { AbsenteeismMe }
}
```

### `attendance` (AttendanceSummary)
| Field | Type |
|---|---|
| `from_date`, `to_date` | string |
| `working_days` | int (shift/roster days) |
| `holiday_days` | int |
| `recorded_days` | int (attendance rows) |
| `present_days`, `absent_days`, `leave_days`, `wfh_days`, `office_days` | int |
| `late_days` | int |
| `total_hours` | float |
| `avg_hours_per_day` | float |
| `attendance_pct` | float (`present ÷ working`) |
| `by_status` | `{ "present": 6, ... }` |
| `daily` | array (see below) |

`daily[]` — one entry per relevant day:
| Field | Notes |
|---|---|
| `date` | `YYYY-MM-DD` |
| `status` | `present` / `leave` / `holiday` / `absent` |
| `work_location` | `OFFICE` / `WFH` / ... (present days only) |
| `check_in`, `check_out` | ISO datetime or null |
| `hours` | float or null |
| `is_late` | bool |

### `punctuality` (PunctualityMe)
`from_date`, `to_date`, `late_days`, `total_checkins`, `avg_check_in` (`"HH:MM"`),
`earliest_check_in` `{date,time}`, `latest_check_in` `{date,time}`,
`by_weekday` `{Mon..Sun}`, `trend[]` `{date, late, check_in}`.

### `absenteeism` (AbsenteeismMe)
`from_date`, `to_date`, `absent_days`, `longest_streak`, `streaks[]` `{start,end,length}`,
`by_weekday`, `monday_absences`, `friday_absences`.

---

## 2. Tickets — `GET /api/analytics/tickets/member`

```json
{ "user": { ... }, "tickets": { TicketSummaryMe } }
```

`tickets`:
| Field | Notes |
|---|---|
| `total` | assigned ∪ authored (all-time; `range` → created-in-range cohort) |
| `assigned_to_me`, `created_by_me` | all-time |
| `open`, `pending`, `closed`, `overdue` | header split (all-time) |
| `open_now`, `overdue_now` | current state |
| `created_in_range`, `closed_in_range`, `authored_in_range` | range-scoped |
| `avg_resolution_days` | float or null |
| `by_status`, `by_priority`, `by_tracker` | `[{ id, name, count }]` |
| `trend` | `[{ date, created, closed }]` |

---

## 3. Leaves — `GET /api/analytics/leaves/member`

```json
{ "user": { ... }, "leaves": { LeaveSummaryMe } }
```

`leaves`:
| Field | Notes |
|---|---|
| `applied` | requests overlapping the range |
| `approved`, `pending`, `rejected`, `cancelled` | status buckets |
| `approved_days` | approved days that fall on the person's shift days |
| `by_type` | `[{ type, requests, days }]` |
| `by_status` | `[{ status, count }]` |
| `trend` | `[{ date, applied, approved }]` |
| `monthly` | `[{ month, applied, approved, approved_days }]` |

---

## 4. WFH — `GET /api/analytics/wfh/member`

```json
{ "user": { ... }, "wfh": { WfhSummaryMe } }
```

`wfh`:
| Field | Notes |
|---|---|
| `total` | WFH requests overlapping the range |
| `approved`, `pending`, `rejected` | status buckets |
| `approved_days` | approved days that fall on the person's shift days |
| `by_status` | `[{ status, count }]` |
| `trend` | `[{ date, applied, approved }]` |
| `monthly` | `[{ month, total, approved, approved_days }]` |

---

## Frontend usage

- Team rows give `redmine_user_id` — pass it to these endpoints (do not use email).
- Disable the drill-down when the team row has `has_login: false`.
- One call per tab: attendance / tickets / leaves / wfh.
- Dates: default is current month; pass `from_date`/`to_date` to change the window (max 366 days).
