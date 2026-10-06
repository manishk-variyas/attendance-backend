# Data model (key tables)

Schemas: `public` (app data) and `redmine` (Redmine data).

## public
| Table | Key columns |
|---|---|
| `employee_master` | `keycloak_user_id`, `redmine_user_id`, `user_email`, `is_active`, `joining_date`, `country`, `location_id` |
| `attendance` | `keycloak_user_id`, `attendance_date`, `status` (enum), `work_location_status` (enum), `check_in_time`, `check_out_time`, `total_hours`, `is_late`, `is_deleted` |
| `shifts` | `keycloak_user_id`, `date`, `shift_code`, `work_location_status` (enum), `status`, `location_id` |
| `leaves` | `keycloak_user_id`, `user_email`, `leave_type`, `approval_status`, `start_date`, `end_date` |
| `wfh_requests` | `keycloak_user_id`, `status`, `start_date`, `end_date` |
| `holidays` | `holiday_date`, `holiday_name`, `holiday_type`, `is_national`, `country_code` |
| `leave_types` | `code`, `name`, `is_paid` |
| `office_locations` | `name`, `radius_meters`, `geom` |
| `sessions` | session store (id, user_sub, data, is_active, expires_at, last_activity_at) |
| `login_attempts` | brute-force counters (identifier, kind, failure_count, blocked_until) |

## redmine
`users` (+ `email_addresses`), `issues` (+ `issue_statuses`, `trackers`,
`enumerations`), `projects`, `members`, `roles`, `member_roles`.

## Important enums (DB types)
- `attendance_status`: `present, absent, half_day, on_leave, holiday`
- `work_location`: `OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE, LEAVE`
- `leaves.approval_status` is **varchar** (approved/pending/rejected/cancelled/…)
- `leaves.leave_type` is **varchar** (EL/CL/SL/CO/UPL/…)

## Links
- `employee_master.keycloak_user_id` ↔ Keycloak `sub` (session identity).
- `employee_master.redmine_user_id` ↔ `redmine.users.id`.
- Analytics resolve a user by **email** (`redmine.email_addresses.address`).
