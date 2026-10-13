# Analytics — Team Tickets API

## Endpoint

```
GET /api/analytics/tickets/team
```

- **Auth:** session cookie
- **Role:** Admin / Project Manager / Project Coordinator only (`403` otherwise)
- **Rate limit:** 30 requests / minute

## Query params

| Param | Type | Required | Notes |
|---|---|---|---|
| `from_date` | string | no | `YYYY-MM-DD`, default = first day of current month |
| `to_date` | string | no | `YYYY-MM-DD`, default = today (max 366-day range) |
| `project_id` | int | no | narrow to one project |
| `search` | string | no | filter members by name (max 100) |
| `scope` | string | no | `current` (default) = all-time state · `range` = over tickets created in range |

## Response

```json
{
  "from_date": "2026-09-01",
  "to_date": "2026-09-30",
  "member_count": 7,
  "members": [
    { "redmine_user_id": 32, "name": "Vishal Singh", "email": "vishal.singh@variyaslabs.com",
      "has_login": true, "open_now": 12, "overdue_now": 4, "created_in_range": 6, "closed_in_range": 1 }
  ],
  "by_project": [
    { "project_id": 8, "project_name": "VAPT TOOL", "total": 4, "open": 4, "closed": 0 }
  ],
  "by_status":   [ { "id": 1, "name": "New",         "count": 69 } ],
  "by_priority": [ { "id": 3, "name": "High P1",     "count": 53 } ],
  "by_tracker":  [ { "id": 1, "name": "Bug",         "count": 46 } ],
  "totals": {
    "total": 98, "open": 87, "closed": 10,
    "open_now": 87, "overdue_now": 35,
    "created_in_range": 50, "closed_in_range": 5
  }
}
```

## Field reference

### `members[]`
| Field | Type | Notes |
|---|---|---|
| `redmine_user_id` | int | pass to the `/…/member` endpoints |
| `name`, `email` | string | |
| `has_login` | bool | disable drill-down when `false` |
| `open_now` | int | assigned, not closed |
| `overdue_now` | int | assigned, not closed, `due_date < today` |
| `created_in_range` | int | assigned tickets created in range |
| `closed_in_range` | int | assigned tickets closed in range |

### `by_project[]`
`{ project_id, project_name, total, open, closed }` — `open + closed = total`.

### `by_status[]` / `by_priority[]` / `by_tracker[]`
`{ id, name, count }` — same shape as the per-employee breakdowns, so the frontend
can reuse one component. Labels come from Redmine (e.g. `High P1`, `Critical P0`).

`by_status[]` also carries the **status × priority cross-tab** in `by_priority`:
```json
"by_status": [
  { "id": 1, "name": "Open", "count": 51,
    "by_priority": [
      { "id": 3, "name": "High P1",   "count": 18 },
      { "id": 4, "name": "Medium P2", "count": 21 },
      { "id": 5, "name": "Low P3",    "count": 12 }
    ] }
]
```
`count` always equals the sum of its `by_priority` counts.

### `authored_by_priority[]`
Priority split of tickets **authored by** the team members in scope:
`[{ id, name, count }]`.

### `totals`
| Field | Meaning |
|---|---|
| `total` | all tickets in scope (`open + closed`) |
| `open` | all not-closed (= `open_now`) |
| `closed` | all closed |
| `open_now` | current not-closed |
| `overdue_now` | current overdue |
| `created_in_range` | created within `from_date..to_date` |
| `closed_in_range` | closed within `from_date..to_date` |

## `scope` behaviour
- `scope=current` (default): `members`, `by_project`, `by_status`, `by_priority`, `by_tracker`, `open_now`, `overdue_now`, `total/open/closed` are **all-time**.
- `scope=range`: those are restricted to tickets **created in the selected range** (so all the breakdown sums equal `created_in_range`).

## Errors
| Status | Meaning |
|---|---|
| `400` | bad date / range > 366 days / invalid `scope` |
| `403` | role not allowed, or PM/PC filtering by a project they're not in |

## Frontend usage
- Header cards: **Open now** = `totals.open_now` · **Overdue** = `totals.overdue_now` · **Created** = `totals.created_in_range` · **Closed** = `totals.closed` (all-time, matches `by_project`).
- Breakdown charts: `by_status` / `by_priority` / `by_tracker` (each sums to the cohort total).
- Project list: `by_project`.
- Member table: `members` (row click → `/…/member` with `redmine_user_id`, skip when `has_login: false`).
