# Database Tables & Relationships (PostgreSQL)

## Overview

This document describes the **target PostgreSQL schema** defined via SQLAlchemy models in `app/models/`. The application is currently migrating from MongoDB to PostgreSQL.

| Data Layer | Status |
|------------|--------|
| **PostgreSQL (App)** — `attendance_app` | Target — models defined in `app/models/` |
| **PostgreSQL (Keycloak)** — `keycloak` | External — managed by Keycloak |
| **Redmine (PostgreSQL)** | External — accessed via REST API |
| **MinIO (S3)** | Object storage — no change |
| **MongoDB** — `attendance_db` | Legacy — being migrated from |

---

## Application PostgreSQL Tables

### 1. `shifts`
Employee shift records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK, default `uuid4` | Primary key (replaces ObjectId) |
| `user_id` | `INTEGER` | NOT NULL, INDEX | Redmine user ID |
| `user_name` | `VARCHAR(255)` | NOT NULL | Display name |
| `user_email` | `VARCHAR(255)` | NOT NULL, INDEX | Email (links to Redmine user) |
| `project_id` | `INTEGER` | NOT NULL, INDEX | Redmine project ID |
| `project_name` | `VARCHAR(255)` | NOT NULL | Project name |
| `shift` | `VARCHAR(50)` | NOT NULL | Shift type code |
| `work_status` | `VARCHAR(50)` | NOT NULL | OFFICE, WFH, ACTIVE, LEAVE, etc. |
| `work_address` | `VARCHAR(500)` | DEFAULT 'N/A' | Work location |
| `leave_type` | `VARCHAR(50)` | NULLABLE | Leave type if status=LEAVE |
| `date` | `VARCHAR(10)` | NOT NULL, INDEX | YYYY-MM-DD |
| `end_date` | `VARCHAR(10)` | NULLABLE | End date for multi-day |
| `shift_start_time` | `VARCHAR(5)` | NOT NULL | HH:MM |
| `shift_end_time` | `VARCHAR(5)` | NOT NULL | HH:MM |
| `shift_start_utc` | `TIMESTAMPTZ` | NULLABLE | UTC start |
| `shift_end_utc` | `TIMESTAMPTZ` | NULLABLE | UTC end |
| `shift_name` | `VARCHAR(100)` | NOT NULL | Shift definition name |
| `timezone` | `VARCHAR(50)` | DEFAULT 'Asia/Kolkata' | IANA timezone |
| `country` | `VARCHAR(10)` | DEFAULT '' | Country code |
| `created_at` | `TIMESTAMPTZ` | DEFAULT now() | |
| `updated_at` | `TIMESTAMPTZ` | DEFAULT now(), on update | |

**Relationships:**
- `user_email` ↔ Redmine `users.mail`
- `project_id` ↔ Redmine `projects.id`

---

### 2. `shift_definitions`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `shift_code` | `VARCHAR(50)` | NOT NULL, UNIQUE, INDEX | e.g. "M1", "GN", "N" |
| `shift_name` | `VARCHAR(255)` | NOT NULL | |
| `start_time` | `VARCHAR(5)` | NOT NULL | HH:MM |
| `end_time` | `VARCHAR(5)` | NOT NULL | HH:MM |
| `timezone` | `VARCHAR(50)` | DEFAULT 'Asia/Kolkata' | |
| `country` | `VARCHAR(10)` | DEFAULT '' | |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | |

**Constraints:** `UNIQUE(shift_code)` — `uq_shift_definition_code`

---

### 3. `leaves`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `VARCHAR(255)` | NOT NULL, INDEX | Keycloak sub (UUID) |
| `user_email` | `VARCHAR(255)` | NOT NULL, INDEX | |
| `start_date` | `TIMESTAMPTZ` | NOT NULL | |
| `end_date` | `TIMESTAMPTZ` | NOT NULL | |
| `leave_type` | `ENUM` | NOT NULL | EL, PL, UPL |
| `reason` | `VARCHAR(1000)` | NULLABLE | |
| `comment` | `VARCHAR(1000)` | NULLABLE | |
| `is_traveling` | `BOOLEAN` | NULLABLE | |
| `contact_number` | `VARCHAR(50)` | NULLABLE | |
| `resuming_date` | `TIMESTAMPTZ` | NULLABLE | |
| `leave_dates` | `TIMESTAMPTZ[]` | NULLABLE | PostgreSQL array |
| `approver_id` | `INTEGER` | NULLABLE | Redmine user ID of PM |
| `status` | `ENUM` | NOT NULL, DEFAULT 'pending' | pending, approved, rejected |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | NULLABLE | |
| `approved_at` | `TIMESTAMPTZ` | NULLABLE | |
| `approved_by` | `VARCHAR(255)` | NULLABLE | |
| `approved_by_role` | `VARCHAR(50)` | NULLABLE | |
| `rejected_at` | `TIMESTAMPTZ` | NULLABLE | |
| `rejected_by` | `VARCHAR(255)` | NULLABLE | |
| `rejected_by_role` | `VARCHAR(50)` | NULLABLE | |

**Enums:**
- `leavetype`: `EL`, `PL`, `UPL`
- `leavestatus`: `pending`, `approved`, `rejected`

**Relationships:**
- `user_email` ↔ Redmine `users.mail`
- `approver_id` ↔ Redmine `users.id`
- `user_id` ↔ Keycloak user `sub`

---

### 4. `holidays`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `country_code` | `VARCHAR(10)` | NOT NULL | e.g. "IN" |
| `region` | `VARCHAR(100)` | NULLABLE | e.g. "KA" |
| `holiday_date` | `VARCHAR(10)` | NOT NULL | YYYY-MM-DD |
| `holiday_name` | `VARCHAR(255)` | NOT NULL | |
| `holiday_type` | `VARCHAR(50)` | NOT NULL | GAZETTED, RESTRICTED |
| `is_national` | `BOOLEAN` | DEFAULT false | |

**Constraints:** `UNIQUE(country_code, holiday_date)` — `uq_holiday`

---

### 5. `leave_balances`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `VARCHAR(255)` | NOT NULL, UNIQUE, INDEX | Keycloak sub |
| `total_earned` | `FLOAT` | DEFAULT 12.0 | |
| `used_earned` | `FLOAT` | DEFAULT 0.0 | |
| `total_paid` | `FLOAT` | DEFAULT 5.0 | |
| `used_paid` | `FLOAT` | DEFAULT 0.0 | |
| `total_unpaid` | `FLOAT` | DEFAULT 0.0 | |
| `used_unpaid` | `FLOAT` | DEFAULT 0.0 | |

---

### 6. `notifications`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `VARCHAR(255)` | NOT NULL, INDEX | Recipient email |
| `type` | `VARCHAR(50)` | NOT NULL | leave_applied, leave_approved, etc. |
| `message` | `VARCHAR(1000)` | NOT NULL | |
| `from_user` | `VARCHAR(255)` | NOT NULL | Actor email |
| `reference_id` | `VARCHAR(255)` | NULLABLE | Related leave ID |
| `is_read` | `BOOLEAN` | DEFAULT false | |
| `created_at` | `TIMESTAMPTZ` | | |

**Relationships:**
- `reference_id` → `leaves.id`
- `user_id` ↔ Redmine `users.mail`
- `from_user` ↔ Redmine `users.mail`

---

### 7. `recordings`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `email` | `VARCHAR(255)` | NOT NULL, INDEX | Owner email |
| `ticket_id` | `VARCHAR(50)` | NULLABLE | Redmine issue ID |
| `project` | `VARCHAR(255)` | NULLABLE | |
| `priority` | `VARCHAR(50)` | NULLABLE | |
| `status` | `VARCHAR(50)` | NULLABLE | |
| `filename` | `VARCHAR(500)` | NOT NULL | |
| `recording_url` | `VARCHAR(1000)` | NOT NULL | MinIO object URL |
| `is_played` | `BOOLEAN` | DEFAULT false | |
| `created_at` | `TIMESTAMPTZ` | | |
| `redmine_details` | `JSON` | NULLABLE | Enriched Redmine metadata |

**Relationships:**
- `email` ↔ Redmine `users.mail`
- `ticket_id` ↔ Redmine `issues.id`
- `recording_url` → MinIO (bucket: `recordings`)

---

### 8. `locations`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `email` | `VARCHAR(255)` | NOT NULL, UNIQUE, INDEX | |
| `latitude` | `FLOAT` | NOT NULL | |
| `longitude` | `FLOAT` | NOT NULL | |
| `updated_at` | `TIMESTAMPTZ` | | |

**Constraint:** `UNIQUE(email)` — one location per user

---

### 9. `system_settings`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `VARCHAR(50)` | PK | Always `"company"` |
| `company_name` | `VARCHAR(255)` | DEFAULT '' | |
| `logo_content_type` | `VARCHAR(100)` | NULLABLE | MIME type |
| `updated_at` | `TIMESTAMPTZ` | | |

**Relationships:** Logo stored in MinIO (bucket: `company-assets`, object: `logo`)

---

## External Systems

### Keycloak (PostgreSQL — realm: `attendance-app`)
Users with roles: `Admin`, `Project Manager`, `Project Coordinator`, `Technical Resource`.
- `email` ↔ Redmine `users.mail` (synced on signup)
- `sub` referenced by `leaves.user_id`, `leave_balances.user_id`

### Redmine (PostgreSQL — accessed via REST API)
- **`users`** — `id`, `login`, `mail`, `firstname`, `lastname`
- **`projects`** — `id`, `name`, `identifier`, custom fields (City, Office Location, Project Type)
- **`members`** — `user_id` ↔ `project_id` (many-to-many)
- **`issues`** — `id`, `subject`, `project_id`, `assigned_to_id`

### MinIO (S3)
- Bucket `recordings` — audio files (path: `{username}/{date}/{time}_{uuid}.ext`)
- Bucket `company-assets` — company logo (object: `logo`)

---

## Entity Relationship Diagram

```
Keycloak (Auth)          Redmine (Projects)         PostgreSQL (App)            MinIO
═══════════              ═════════════════          ════════════════            ══════
users                    users ─────────────────► shifts                       recordings
  sub (UUID)               │  id, mail              user_email, project_id       ▲
  email ───────────────►   │                        user_id                      │
  username                 │                                                    │
                           ├── projects ◄─────────► shifts.project_id          company-assets
                           │  id, name              leaves                       ▲
                           │                        user_email                   │
                           ├── members ◄──────────► leaves.approver_id         system_settings
                           │  user_id               notifications ◄──┘           logo
                           │  project_id             reference_id
                           ├── issues ◄───────────► recordings
                           │  id                     ticket_id, email
                           │  assigned_to_id        locations
                           │                         email
                           ├── projects.name ─────► recordings.project
                           │
                           └── users.id ──────────► leaves.approver_id
                                                    leave_balances.user_id
```

## Feature-to-Table Mapping

| Feature | Tables Used | External Dependencies |
|---------|-------------|----------------------|
| Auth | — | Keycloak (users, roles) |
| Shifts | shifts, shift_definitions | Redmine (users, projects) |
| Leaves | leaves, holidays, leave_balances | Redmine (users, projects), Notifications |
| Notifications | notifications | Redmine (users) |
| Recordings | recordings | Redmine (users, issues, projects), MinIO |
| Location | locations | — |
| Settings | system_settings | MinIO |
| Redmine Sync | — | Redmine (users, projects, members, issues) |
