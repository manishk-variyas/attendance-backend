# Admin Create Leave API Reference

Admin creates leave on behalf of an employee (e.g. sick, unreachable).
Base URL prefix: `/api`

```
POST /api/leaves/create
Content-Type: application/json
```

Admin-only. Any non-admin role gets `403`. Same validations as self-apply
(`POST /leaves/apply`) — the shared service is reused, so overlap,
completed-attendance, leave-type, and resuming-date rules behave identically.

---

## Full payload — every accepted key

```json
{
  "user_email": "sick.person@variyaslabs.com",
  "leave_type": "EL",
  "start_date": "2026-09-20T00:00:00Z",
  "end_date": "2026-09-21T00:00:00Z",
  "reason": "Down with fever, unreachable",
  "comment": "Optional internal note, max 1000 chars",
  "is_traveling": false,
  "contact_number": "9876543210",
  "resuming_date": "2026-09-22T00:00:00Z",
  "leave_dates": ["2026-09-20T00:00:00Z", "2026-09-21T00:00:00Z"],
  "approver_id": 21,
  "auto_approve": true
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `user_email` | string | **yes** | target employee's email; must exist, active, with a linked login |
| `leave_type` | string | **yes** | uppercased automatically; must be an active type (`EL`, `UPL`, `PL`, …) |
| `start_date` | datetime | **yes** | ISO datetime |
| `end_date` | datetime | **yes** | ISO datetime |
| `reason` | string | no | max 1000 chars |
| `comment` | string | no | max 1000 chars |
| `is_traveling` | bool | no | |
| `contact_number` | string | no | max 50 chars |
| `resuming_date` | datetime | no | must be after the last leave day, else `400` |
| `leave_dates` | array of datetime | no | cherry-picked days instead of a range |
| `approver_id` | int | no | Redmine user id of approver; default: target's `reports_to` manager |
| `auto_approve` | bool | no | default `false`; `true` = approve immediately (no approver step) |

Behavior notes:
- Backdated (sick) leaves allowed — worked/checked-out days are still blocked, same as self-apply.
- One leave row is created per day; `leave_ids` returns all of them.
- `auto_approve: true` skips the "new request" email to the approver and sends a single approved email to the employee (multi-day = 1 email, not N).
- `auto_approve: false` sends confirmation to the employee + applied email to the approver + in-app notifications to both.
- Balance is derived on read (no deduction step); response includes the balance preview.

---

## Success response `200`

```json
{
  "message": "Leave created successfully",
  "leave_id": "a7bc2cfc-284c-47b0-b56b-b4dfd330d999",
  "leave_ids": ["a7bc2cfc-284c-47b0-b56b-b4dfd330d999"],
  "created": 1,
  "balance": { "requested_days": 1, "remaining": 2.0, "will_be_negative": false },
  "target_email": "abhilash@variyaslabs.com",
  "auto_approved": true
}
```

---

## Error responses

| Status | Body | Meaning |
|---|---|---|
| `403` | `{ "detail": "Requires 'Admin' role" }` | caller is not an Admin |
| `404` | `{ "detail": "Employee not found: <email>" }` | no `employee_master` row for `user_email` |
| `400` | `{ "detail": "Employee account is not active: <email>" }` | target disabled/inactive |
| `400` | `{ "detail": "Employee has no linked login account: <email>" }` | target has no `keycloak_user_id` |
| `400` | `{ "detail": "Invalid or inactive leave type: <type>" }` | bad `leave_type` |
| `400` | `{ "detail": "Resuming date must be after the last leave day." }` | bad `resuming_date` |
| `400` | `{ "detail": "You already worked on <date>. Leave can only be applied before your shift starts." }` | checked-out attendance on those dates |
| `400` | `{ "detail": "You already have an approved or pending leave application that overlaps with these dates." }` | overlap (or `You already have a leave on <date>` for `leave_dates`) |
| `422` | `{ "detail": [{ "field": "<name>", "message": "<reason>" }] }` | schema violation (bad date format, overlong field, missing required) |
| `429` | rate-limit payload | over 10 requests/minute |

## Audit

Every call is audit-logged (`action: admin_create_leave`) with admin email,
target email, created leave IDs, and the `auto_approve` flag. When
`auto_approve` is true, `approved_by` on the rows is the admin's email.
