# SQL Injection Audit — Backend (FastAPI)

**Date:** 2026-10-07
**Scope:** `apps/backend/app/**` (all Python)
**Verdict:** No exploitable SQL injection found. **1 low-risk hardening item.**
**Update 2026-10-10:** input-validation hardening applied (see § "Input validation").

---

## Method

Searched the whole backend for every way SQL text can be built or executed:

- `text(f"...")`, `execute(f"...")`, `text(variable)`
- string concatenation (`+`) / `.format()` / `%` into SQL
- dynamic identifiers: `ORDER BY`, `date_trunc(...)`, `LIMIT`, table/column names

Then traced the **source of every interpolated value** to see if it can be
attacker-controlled (user input) or is a constant / whitelist / DB value.

---

## Summary

| # | Finding | Risk | Status |
|---|---|---|---|
| SQLI-01 | String-built `IN (...)` list in project delete-confirm | Low (hardening) | Open |

Everything else is safe: runtime values are **bound parameters** (`:name`), and
all dynamic identifiers are **whitelisted**.

---

## SQLI-01 — String-built `IN (...)` list (Low / hardening)

**File:** `apps/backend/app/features/redmine/routes.py:592-594`

```python
placeholders = ", ".join(f"'{str(i)}'" for i in ids)
rows = db.execute(
    text(f"SELECT id, name FROM redmine.projects WHERE id::text IN ({placeholders})")
).fetchall()
```

**Why it is low risk (not currently exploitable):**
- `ids = json.loads(token.ids_json)` (line 585).
- `ids_json` is written in only one place — `routes.py:534` —
  `json.dumps([r["project_id"] for r in resolved])`, where each value is
  `redmine.projects.id` (an **integer read from the DB**).
- So the interpolated values are always DB integers, not user input. The
  endpoint is also Admin-only and OTP-gated.

**Why it is still worth fixing:** the pattern is fragile. If the source of `ids`
ever changes to raw request data, it becomes a real injection.

**Fix (parameterize):**

```python
rows = db.execute(
    text("SELECT id, name FROM redmine.projects WHERE id = ANY(:ids)"),
    {"ids": [int(i) for i in ids]},
).fetchall()
```

---

## Verified-safe patterns (no action needed)

- **ORM everywhere else** — `db.query(...)`, `select(...)`, `filter(...)` are
  parameterized by SQLAlchemy.
- **Analytics dynamic SQL** (`features/analytics/service.py`) interpolates only:
  - `_ATT_PRESENT` / `_ATT_LEAVE` / `_ATT_WFH` — fixed constant strings
  - `trunc` — set by `if group_by == "day"/"week"/"month"` (route validates `group_by` against `_GROUP_KEYS`)
  - `sort_map[sort_by]` — route validates `sort_by` against `_SORT_KEYS`
  - `direction` — route validates `sort_dir` in `("asc","desc")`
  - `scope_where` — built server-side in `team_scope()`, values via `:ids` / `:search`
- **`features/redmine/sql_service.py`** — `where_sql` assembled from fixed clauses
  with `:params`; `sort_col` from the `_ISSUE_SORT_COLUMNS` whitelist; `direction`
  is `ASC`/`DESC`; search escaped for `%`/`_` and bound; project admin search uses
  `:search_term` / `:city_term` / `:pt_term`.
- **`utils/holiday_scope.py`** — interpolates a fixed clause string
  (`is_national = true OR (region IS NULL OR region = :h_state)`); the state value
  is bound via `bindparams`.
- **`features/redmine/routes.py:514`** — `WHERE id::text = :v OR identifier = :v`
  (bound).
- **Employee deletes** (`features/employees/routes.py`) — `WHERE id = :id`,
  `WHERE user_id = :uid` (bound).
- **Migrations** (`core/database.py`) — static SQL only.
- **LIKE searches** — always `:param` with `%term%`, never interpolated.

---

## Recommendations

1. Apply the SQLI-01 parameterization.
2. Keep the whitelist approach for any future sort/order/group parameters.
3. Optional CI guard: fail the build if an f-string is passed to `text(...)` or
   `.execute(...)`.

---

## Input validation hardening (applied 2026-10-10)

Follow-up sweep for unconstrained user input (length/format). No SQLi or auth
bypass was found; the class of issue was **unbounded input → `500`s on write,
and unescaped LIKE wildcards on search**.

### Write-input caps (oversized value used to 500 at the DB layer)
| Input | Cap added | Matches DB column |
|---|---|---|
| `location` GET param + POST `email` | `EmailStr`, `max_length=255` | `user_locations.email` VARCHAR(255) |
| attendance admin remarks + check-in/out location names | `max_length=500` | `attendance.*` VARCHAR(500) |
| attendance admin `status` / `work_location_status` | `Literal[...]` of the enum sets | `attendance` SAEnums |
| leave apply `reason` / `comment` | `max_length=1000` | `leaves.*` VARCHAR(1000) |
| wfh `comment` (create + update) | `max_length=500` | `wfh_requests.comment` VARCHAR(500) |

Invalid values now fail fast with `422` before any DB write (verified live).

### Search caps
- Redmine issue/global search `q` and employees `/suggest` `q`: `max_length=100`
  (consistent with every other search box; services already truncate to 100).
- `int()` parsing of comma-separated ids was already guarded with `400`
  (`redmine/routes.py`, `employees/service.py`) — verified, no change needed.

### LIKE-wildcard escaping
New shared helper `app/utils/sanitize.py::escape_like()` (escapes `\`, `%`, `_`).
Applied everywhere a user term is wrapped in `%…%`, with `ESCAPE '\'` on raw SQL
and `escape="\\"` on ORM `ilike()`:
analytics, attendance admin, employees suggest, leaves (routes/service/leave-types),
location admin, redmine issues/projects/city/type search, shifts, states, wfh
(routes/service), shift service. A bare `%` search now matches literally (0 rows)
instead of everything in scope.

### Verification (2026-10-10)
- `GET /api/location/233232` → `422` (was 403/404); valid email still `200`.
- 600-char attendance remarks → `422` (was potential `500`); 1000-char leave
  reason and 500-char wfh comment accepted at the boundary.
- 101-char redmine `q` → `422`; normal searches return results.
- `%` as team search → 0 members (literal, not wildcard).
- Regression suites: **76/76** (verify1 15, verify2 16, verify3 21, verify4 24).
