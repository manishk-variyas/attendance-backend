# Leave Balance Manager — Implementation Plan

> Status: DRAFT — awaiting final decisions on open questions (Section 8)

## 1. Goal

Replace the hardcoded 3-type `leave_balances` table (EL / PL / UPL baked into columns) with a
generic, per-type leave balance system backed by the `leave_types` table.

- HR fills **monthly accrual** and **carry-forward** manually per employee per leave type.
- Employees see **computed** `TOTAL` / `AVAILED` / `BALANCE` per type.

## 2. Core Principle

- **No accrual formula.** Admin types raw monthly values and carry-forward by hand.
- The only arithmetic is on the **read side** (summing + subtracting) to produce the dashboard.

| Value | Stored? | How |
|---|---|---|
| monthly `accrued` | ✅ stored | admin fills per month |
| yearly `carry_forward` | ✅ stored | admin fills per year |
| `TOTAL` | ❌ computed | `carry_forward + Σ(monthly.accrued)` |
| `AVAILED` | ❌ computed | `Σ(approved leave days)` from `leaves` |
| `BALANCE` | ❌ computed | `TOTAL − AVAILED` |

## 3. Data Model (3 tables)

```
leave_types               (already built: code, name, is_paid, carry_forward_*)
   │ leave_type_id
   ▼
leave_balances            (yearly row: user + type + fiscal_year)
   │ leave_balance_id
   ▼
leave_balance_monthly     (12 rows: Apr..Mar, each with accrued)
```

### 3.1 `leave_types` — (exists)

| Column | Type |
|---|---|
| id | UUID PK |
| code | varchar(20) unique |
| name | varchar(100) unique |
| is_paid | bool |
| carry_forward_allowed | bool |
| carry_forward_cap | float nullable |
| is_active | bool |
| created_at / updated_at | timestamptz |

Seeded: CL, EL, SL, MPL, SBL, CO, UPL.

### 3.2 `leave_balances` — yearly (NEW)

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| keycloak_user_id | varchar(255) | FK → employee_master |
| leave_type_id | UUID | FK → leave_types |
| fiscal_year | int | e.g. 2026 = Apr 2026 – Mar 2027 |
| carry_forward | float | admin fills |
| modified_by | varchar(255) | |
| created_at / updated_at | timestamptz | |
| **UNIQUE** | (keycloak_user_id, leave_type_id, fiscal_year) | |

### 3.3 `leave_balance_monthly` — monthly (NEW)

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| leave_balance_id | UUID | FK → leave_balances |
| month | int | 1 = Apr … 12 = Mar |
| accrued | float | admin fills |
| **UNIQUE** | (leave_balance_id, month) | |

## 4. Fiscal Year

Columns run **Apr → Mar** (fiscal year, not calendar).

- Store `fiscal_year` (e.g. `2026` = Apr 2026 – Mar 2027).
- Store `month` as `1..12` where `1 = Apr`, `12 = Mar`.

> OPEN: confirm fiscal year vs calendar year (Section 8, Q1).

## 5. Admin Fill API (WRITE side)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/admin/leave-balances?fiscal_year=&user_email=` | load the grid (types + 12 months + carry) |
| POST | `/api/admin/leave-balances/bulk` | save the whole grid in one shot |

Admin only, rate-limited, audit-logged. Upserts yearly + monthly rows.

Bulk payload (spreadsheet-shaped):

```json
{
  "user_email": "manishk@variyaslabs.com",
  "fiscal_year": 2026,
  "types": [
    {
      "code": "CL",
      "carry_forward": 0,
      "months": {
        "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1,
        "7": 1, "8": 1, "9": 1, "10": 1, "11": 1, "12": 1
      }
    }
  ]
}
```

## 6. Employee Read API (READ side)

Rewrite `GET /api/leaves/balance` → generic per-type array (loops `leave_types`, active only).

```json
{
  "employee_id": "EMP-TR-001",
  "as_of_date": "2026-08-14",
  "leave_balances": [
    { "code": "CL", "name": "Casual Leave", "total": 12, "availed": 3, "balance": 9 },
    { "code": "EL", "name": "Earned Leave", "total": 23, "availed": 1, "balance": 22 }
  ],
  "total_available_leave": 31
}
```

Formula per type:

- `total   = carry_forward + Σ(monthly.accrued)`
- `availed = Σ(approved leave days)` from `leaves`
- `balance = total - availed`

## 7. Migration Strategy (staged, non-breaking)

1. Create new `leave_balances` + `leave_balance_monthly` tables (generic).
2. Keep the legacy EL/PL/UPL columns accessible during transition.
3. Build admin fill + read APIs against the new tables.
4. Later: migrate/retire the legacy columns.

> ⚠️ **Name collision:** the current table is already named `leave_balances`. Options:
> - **(A)** Rename old table → `leave_balances_legacy`, new yearly table = `leave_balances`
> - **(B)** Name the new yearly table `employee_leave_balances` (no rename)

## 8. Open Questions

1. **Fiscal year** — store `fiscal_year` (2026 = Apr 2026–Mar 2027) + `month 1–12`? Or calendar year Jan–Dec?
2. **AVAILED** — derived from `leaves` (approved, current fiscal year), not admin-entered. Confirm.
3. **Name clash** — (A) rename old `leave_balances` → `leave_balances_legacy`, or (B) new table = `employee_leave_balances`?
4. **Old `/balance` API** — OK to rewrite to the generic `leave_balances` array (3-field shape removed)?
5. **Admin grid granularity** — bulk `POST` (save whole grid) vs per-type/per-month granular endpoints?

## 9. Files (anticipated)

| File | Change |
|---|---|
| `app/models/leave_balance.py` | new/updated models (`LeaveBalance`, `LeaveBalanceMonthly`) |
| `app/models/leave_type.py` | exists (reference via FK) |
| `app/core/database.py` | migrations (create tables) |
| `app/features/leaves/admin_routes.py` | admin fill endpoints (grid load + bulk save) |
| `app/features/leaves/schemas/leaves.py` | new schemas (bulk payload, per-type response) |
| `app/features/leaves/services/leave_service.py` | rewrite `get_leave_balance` → per-type |
| `app/features/leaves/routes.py` | update `GET /balance` response |
