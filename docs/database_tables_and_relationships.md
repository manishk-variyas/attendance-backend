# Database Tables / Collections & Relationships

## Overview

This application uses a **polyglot persistence** architecture with multiple storage systems:

| System | Purpose |
|--------|---------|
| **MongoDB** | Primary application data (shifts, leaves, notifications, recordings, locations, settings) |
| **Keycloak (PostgreSQL)** | User identity, authentication, roles (managed by Keycloak, not this backend) |
| **Redmine (PostgreSQL)** | Project management (users, projects, memberships, issues — accessed via REST API) |
| **MinIO (S3)** | Object storage (audio recordings, company logo) |
| **In-memory** | User sessions (session_id → user data, temporary) |

---

## MongoDB Collections (`attendance_db`)

### 1. `shifts`
Employee shift records.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `userId` | int | Redmine user ID |
| `userName` | string | Display name |
| `userEmail` | string | Email (links to Redmine user) |
| `projectId` | int | Redmine project ID |
| `projectName` | string | Project name |
| `shift` | string | Shift type code (e.g. "general", "morning", "night") |
| `workStatus` | string | OFFICE, WFH, ACTIVE, LEAVE, PRESENT, CUSTOMER_SITE |
| `workAddress` | string | Work location address |
| `leaveType` | string | Leave type if status=LEAVE |
| `date` | string | Shift date (YYYY-MM-DD) |
| `endDate` | string | End date for multi-day shifts |
| `shiftStartTime` | string | Start time (HH:MM) |
| `shiftEndTime` | string | End time (HH:MM) |
| `shiftStartUTC` | datetime | UTC start timestamp |
| `shiftEndUTC` | datetime | UTC end timestamp |
| `shiftName` | string | Shift definition name |
| `timezone` | string | IANA timezone |
| `country` | string | Country code |
| `createdAt` | datetime | Creation timestamp |
| `updatedAt` | datetime | Last update timestamp |

**Relationships:**
- `userEmail` ↔ Redmine `users.mail`
- `projectId` ↔ Redmine `projects.id`
- One shift per `(userId, date)` — enforced by duplicate guard

---

### 2. `shift_definitions`
Shift type definitions (templates).

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `shiftCode` | string | Unique code (e.g. "M1", "A1", "GN", "N") |
| `shiftName` | string | Display name |
| `startTime` | string | HH:MM |
| `endTime` | string | HH:MM |
| `timezone` | string | IANA timezone |
| `country` | string | Country code |
| `createdAt` | datetime | |
| `updatedAt` | datetime | |

**Relationships:** Referenced by `shifts.shiftName`

---

### 3. `leaves`
Leave applications.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `user_id` | string | Keycloak user sub (UUID) |
| `user_email` | string | Email (links to Redmine user) |
| `start_date` | datetime | Leave start |
| `end_date` | datetime | Leave end |
| `leave_type` | string | EL (Earned), PL (Paid), UPL (Unpaid) |
| `reason` | string | Reason for leave |
| `comment` | string | Additional comment |
| `is_traveling` | bool | |
| `contact_number` | string | |
| `resuming_date` | datetime | |
| `leave_dates` | datetime[] | Specific dates |
| `approver_id` | int | Redmine user ID of approver (PM) |
| `status` | string | pending, approved, rejected |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `approved_at` | datetime | |
| `approved_by` | string | Email of approver |
| `approved_by_role` | string | Admin or Project Manager |
| `rejected_at` | datetime | |
| `rejected_by` | string | |
| `rejected_by_role` | string | |

**Relationships:**
- `user_email` ↔ Redmine `users.mail`
- `approver_id` ↔ Redmine `users.id`
- `user_id` ↔ Keycloak user `sub` (UUID)

---

### 4. `holidays`
Holiday calendar.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `country_code` | string | e.g. "IN" |
| `region` | string | e.g. "KA", "TN" |
| `holiday_date` | string | YYYY-MM-DD |
| `holiday_name` | string | e.g. "Republic Day" |
| `holiday_type` | string | GAZETTED or RESTRICTED |
| `is_national` | bool | |

**Relationships:** Standalone lookup table. Uniqueness: `country_code` + `holiday_date`.

---

### 5. `leave_balances`
Leave balance allocations per user.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `user_id` | string | Keycloak user sub |
| `total_earned` | float | Total EL allocated (default 12.0) |
| `total_paid` | float | Total PL allocated (default 5.0) |
| `total_unpaid` | float | Total UPL allocated (default 0.0) |
| *(more fields possible)* | | |

**Relationships:**
- Referenced by `leaves` stats calculation (`user_id`)

---

### 6. `notifications`
User notifications.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `user_id` | string | Email of recipient |
| `type` | string | leave_applied, leave_approved, leave_rejected |
| `message` | string | Notification text |
| `from_user` | string | Email of actor |
| `reference_id` | string | Related leave application ID (MongoDB _id) |
| `is_read` | bool | |
| `created_at` | datetime | |

**Relationships:**
- `reference_id` ↔ `leaves._id`
- `user_id` (recipient email) ↔ Redmine `users.mail` / `leaves.user_email`
- `from_user` ↔ Redmine `users.mail`

---

### 7. `recordings`
Call recording metadata.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `email` | string | Owner email |
| `ticket_id` | string | Redmine issue ID |
| `project` | string | Redmine project name |
| `priority` | string | Issue priority |
| `status` | string | Issue status |
| `filename` | string | Original filename |
| `recording_url` | string | MinIO object URL |
| `is_played` | bool | |
| `created_at` | datetime | |
| `redmine_details` | object | Enriched Redmine metadata |
| `redmine_details.redmine_project_id` | int | |
| `redmine_details.redmine_status_id` | int | |
| `redmine_details.redmine_priority_id` | int | |
| `redmine_details.subject` | string | Issue subject |

**Relationships:**
- `email` ↔ Redmine `users.mail`
- `ticket_id` ↔ Redmine `issues.id`
- `recording_url` → MinIO object (bucket: `recordings`)

---

### 8. `locations`
User GPS location tracking.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | Primary key |
| `email` | string | User email (unique key for upsert) |
| `latitude` | float | |
| `longitude` | float | |
| `updated_at` | datetime | |

**Relationships:**
- `email` ↔ Redmine `users.mail`

---

### 9. `system_settings`
Company settings.

| Field | Type | Description |
|-------|------|-------------|
| `_id` | string | Always `"company"` |
| `company_name` | string | |
| `logo_content_type` | string | MIME type of stored logo |
| `updated_at` | datetime | |

**Relationships:**
- Logo image stored in MinIO bucket `company-assets` (object name: `logo`)

---

## Keycloak (PostgreSQL — managed by Keycloak)

### `users` (in Keycloak's realm `attendance-app`)

| Attribute | Description |
|-----------|-------------|
| `sub` (UUID) | Unique user ID (stored in JWT) |
| `username` | Login name |
| `email` | Email address |
| `timezone` | Custom attribute (IANA timezone, embedded in JWT) |

**Roles:** `Admin`, `Project Manager`, `Project Coordinator`, `Technical Resource`

**Relationships:**
- `email` ↔ Redmine `users.mail` (synced on signup via background task)
- `username` ↔ Redmine `users.login` (synced on signup)
- MongoDB entities reference `sub` as `user_id`

---

## Redmine (PostgreSQL — accessed via REST API)

### `users`

| Field | Description |
|-------|-------------|
| `id` | Primary key (int) |
| `login` | Username |
| `mail` | Email |
| `firstname` | |
| `lastname` | |
| `password` | (synced on signup) |
| `preferences.time_zone` | IANA timezone |

**Relationships:**
- `mail` ↔ Keycloak `email` (synced)
- `mail` ↔ MongoDB `shifts.userEmail`, `leaves.user_email`, `recordings.email`, `locations.email`, `notifications.user_id`, `notifications.from_user`
- `id` ↔ `projects_members.user_id` (via memberships)
- `id` ↔ `issues.assigned_to_id`

### `projects`

| Field | Description |
|-------|-------------|
| `id` | Primary key (int) |
| `name` | Project name |
| `identifier` | URL-friendly slug |
| `status` | 1=active, 5=closed |
| Custom fields: City, Customer Office Location, Project Type | |

**Relationships:**
- `id` ↔ `projects_members.project_id`
- `id` ↔ `issues.project_id`
- `id` ↔ MongoDB `shifts.projectId`
- `name` ↔ MongoDB `recordings.project`

### `members` (project memberships)

| Field | Description |
|-------|-------------|
| `project_id` | FK → projects |
| `user_id` | FK → users |
| `role_ids` | Array of role IDs |

**Relationships:**
- Many-to-many between `users` and `projects` (a user can be in many projects; a project has many users)

### `issues`

| Field | Description |
|-------|-------------|
| `id` | Primary key (int) |
| `subject` | |
| `description` | |
| `project_id` | FK → projects |
| `assigned_to_id` | FK → users |
| `author_id` | FK → users |
| `status_id` | |
| `priority_id` | |
| `tracker_id` | |
| `created_on` | |
| `updated_on` | |

**Relationships:**
- `project_id` ↔ `projects.id`
- `assigned_to_id` ↔ `users.id`
- MongoDB `recordings.ticket_id` ↔ `issues.id`

---

## MinIO (S3-compatible Object Storage)

### Bucket: `recordings`
Stores audio recording files uploaded via the recordings feature.

Object path format: `{username}/{YYYY-MM-DD}/{HHMMSS}_{short_uuid}.{ext}`

**Relationships:** Referenced by MongoDB `recordings.recording_url`

### Bucket: `company-assets`
Stores the company logo.

Object: `logo`

**Relationships:** Referenced by MongoDB `system_settings`

---

## In-Memory Session Store

### Sessions

| Key | Value |
|-----|-------|
| `session_id` (random token) | `{data: {sub, username, email, roles, kc_refresh_token?}, expires: datetime}` |

**Relationships:**
- `data.sub` ↔ Keycloak user `sub`
- Keycloak refresh token stored for session renewal

---

## Entity Relationship Summary

```
Keycloak (Auth)          Redmine (Projects)         MongoDB (App Data)          MinIO (Files)
═══════════              ═════════════════          ═════════════════           ═════════════
users                    users ─────────────────► shifts                         recordings bucket
  sub (UUID)               │   id, mail              user_id, userEmail            ▲
  username ─────────────►  │   login, mail            projectId                    │
  email                    │                          userId                     recordings
                           ├── projects ◄─────────► shifts.projectId             recording_url
                           │     id, name             leaves                      ▲
                           │     custom_fields         user_email                 │
                           ├── members                 approver_id              company-assets
                           │     user_id ──────────► leaves.approver_id           bucket
                           │     project_id            notifications ▲──┘          ▲
                           ├── issues ◄────────────►   reference_id               │
                           │     id                    recordings ◄──┘           system_settings
                           │     assigned_to_id         ticket_id                  logo
                           │     project_id             email
                           │                           locations
                           │                             email
                           └── projects.name ──────► recordings.project
```

## Cross-Reference: Which Features Use Which Tables

| Feature | MongoDB | Keycloak | Redmine | MinIO |
|---------|---------|----------|---------|-------|
| Auth | — | users, sessions | — | — |
| Shifts | shifts, shift_definitions | users (auth) | users, projects, members | — |
| Leaves | leaves, holidays, leave_balances | users (auth) | users, projects, members | — |
| Notifications | notifications | — | users | — |
| Recordings | recordings | — | users, issues, projects | recordings bucket |
| Location | locations | — | users | — |
| Settings | system_settings | — | — | company-assets bucket |
| Redmine Sync | — | — | users, projects, members, issues | — |
