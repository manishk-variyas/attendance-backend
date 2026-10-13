# Tickets — Status × Priority Cross-Tab & Authored Breakdown

Additive change to the ticket analytics: each status now carries its own priority
split, plus a priority split of **authored** tickets.

Applies to **all three** endpoints:

| Scope | Endpoint |
|---|---|
| Self | `GET /api/analytics/tickets/me` |
| Member drill-down | `GET /api/analytics/tickets/member` |
| Team | `GET /api/analytics/tickets/team` |

Nothing existing was removed — these are new fields only.

---

## 1. `by_status[].by_priority` — status × priority cross-tab

Each item in `by_status` now has a nested `by_priority` array:

```json
"by_status": [
  {
    "id": 1, "name": "Open", "count": 51,
    "by_priority": [
      { "id": 3, "name": "High P1",   "count": 18 },
      { "id": 4, "name": "Medium P2", "count": 21 },
      { "id": 5, "name": "Low P3",    "count": 12 }
    ]
  },
  {
    "id": 2, "name": "Pending", "count": 9,
    "by_priority": [ { "id": 3, "name": "High P1", "count": 3 }, { "id": 4, "name": "Medium P2", "count": 6 } ]
  },
  {
    "id": 3, "name": "Closed", "count": 7,
    "by_priority": [ { "id": 4, "name": "Medium P2", "count": 7 } ]
  }
]
```

**Rule:** for every status, `count == sum(by_priority[].count)`. So you can render
"Open 51 → High 18 / Medium 21 / Low 12".

### Shape
| Field | Type | Notes |
|---|---|---|
| `id` | int | status id |
| `name` | string | status name |
| `count` | int | total in this status |
| `by_priority` | array | `[{ id, name, count }]` per priority |

---

## 2. `authored_by_priority` — priority split of authored tickets

```json
"authored_by_priority": [
  { "id": 3, "name": "High P1",   "count": 12 },
  { "id": 4, "name": "Medium P2", "count": 30 ,
s  { "id": 5, "name": "Low P3",    "count": 8 }
]
```

- **me / member:** tickets **authored by that employee**.
- **team:** tickets **authored by team members in scope**.

---

## Scope behaviour (same as the rest of the endpoint)

- `tickets/me` & `tickets/member`: over the person's cohort (assigned, created in range).
- `tickets/team` with `scope=current` (default): all-time.
- `tickets/team` with `scope=range`: restricted to tickets created in the selected range.
  Then every breakdown's total equals `totals.created_in_range`.

---

## Frontend usage

- Stacked bar / matrix: `by_status` (X = status) × `by_priority` (segments).
- Donut: `by_priority` (marginal totals) — unchanged, still available.
- "Authored by priority": `authored_by_priority`.
- Existing components keep working; just read the new nested arrays.
