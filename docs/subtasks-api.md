# Subtasks API Reference

Subtask (child ticket) support for the Redmine integration.
Base URL prefix: `/api`

---

## 0. Which endpoints return subtask info (frontend guide)

Every ticket object returned by the APIs below carries both fields:

- `parent_issue_id: int | null` — `null` = top-level ticket, a number =
  subtask of that ticket id.
- `subtasks_count: int` — `0` = no subtasks. Computed live per row
  (no caching), so it is always accurate.

| Endpoint | `parent_issue_id` | `subtasks_count` |
|---|---|---|
| `GET /api/team-issues` | yes | yes |
| `GET /api/my-issues/paginated` | yes | yes |
| `GET /api/redmine/issues/{id}` (detail) | yes | yes |
| `GET /api/redmine/issues/{id}/subtasks` | yes (points back to the parent) | yes (each child has its own count, so nesting works recursively) |
| Project issues / user issues | yes | yes |
| Excel exports (`export=true`) | yes (column) | yes (column) |

### Recommended UI flow

1. Ticket list → read `subtasks_count` on each row → show a badge
   (e.g. `▸ 3 subtasks`) only when it is `> 0`.
2. Click the badge → call `GET /api/redmine/issues/{id}/subtasks` using
   that row's own `id`, render the returned `records` underneath
   (same ticket-row component can be reused — the shape is identical).
3. A row with non-null `parent_issue_id` is itself a subtask → show a
   `subtask of #<id>` link that opens the parent via the detail endpoint.

### Known nuance: subtask count for Technical Resource users

`subtasks_count` is the true Redmine count — all children, regardless of
who is looking. For Admin / PM / PC the subtasks endpoint returns exactly
that many rows, so badge and list always match.

For a **TR user** there can be a mismatch: the count may say `3` while the
subtasks endpoint returns only `1`, because TRs only see children assigned
to them or authored by them. The badge in that case means
"this ticket has subtasks — click to view yours".

---

## 1. List subtasks of a ticket

Returns the child tickets of a parent issue with the same pagination,
filters, response shape, and role-based security as the team-issues endpoint.

```
GET /api/redmine/issues/{issue_id}/subtasks
```

### Query parameters

| Param | Type | Required | Default / limits |
|---|---|---|---|
| `page` | int | no | default `1`, min `1` |
| `page_size` | int | no | default `25`, min `1`, max `100` |
| `sort_by` | string | no | default `updated_on`; one of `updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name` |
| `sort_dir` | string | no | default `desc`; `asc` or `desc` (anything else falls back to `desc`) |
| `status_id` | int | no | filter by status id |
| `priority_id` | int | no | filter by priority id |
| `tracker_id` | int | no | filter by tracker id |
| `search` | string | no | matches subject or author name, trimmed, max 100 chars |
| `from_date` | string | no | `YYYY-MM-DD`, filters `updated_on >= from_date` |
| `to_date` | string | no | `YYYY-MM-DD`, filters `updated_on < to_date + 1 day` |
| `export` | bool | no | default `false`; `true` returns an Excel file download instead of JSON |

### Success response `200`

```json
{
  "records": [
    {
      "id": 140,
      "subject": "Subtask subject here",
      "description": "Full description text or null",
      "status": "New",
      "priority": "High P1",
      "tracker": "Bug",
      "project_id": 34,
      "project_name": "3rInfotech",
      "assigned_to_name": "Manish Mishra",
      "created_on": "2026-09-09T07:26:08.963164",
      "updated_on": "2026-09-09T07:26:08.963164",
      "start_date": "2026-09-09",
      "due_date": null,
      "estimated_hours": null,
      "done_ratio": 0,
      "is_private": false,
      "status_id": 1,
      "priority_id": 3,
      "tracker_id": 1,
      "assigned_to_id": 56,
      "author_id": 56,
      "author_name": "Manish Mishra",
      "attachments": [
        {
          "id": 59,
          "filename": "image.png",
          "content_type": "image/jpeg",
          "filesize": 79649,
          "created_on": "2026-09-09T10:21:27.963949"
        }
      ],
      "custom_fields": [
        { "id": 4, "name": "Customer Name", "value": "" },
        { "id": 5, "name": "Application", "value": "" },
        { "id": 6, "name": "Environment", "value": "" },
        { "id": 7, "name": "Root Cause", "value": "" },
        { "id": 8, "name": "Compliance Category", "value": "" },
        { "id": 9, "name": "Risk Level", "value": "" }
      ],
      "parent_issue_id": 136,
      "subtasks_count": 0
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 25,
  "total_pages": 1
}
```

### Error responses

| Status | Body | Meaning |
|---|---|---|
| `403` | `{ "detail": "Access denied" }` | no role in session |
| `404` | `{ "detail": "User not found in Redmine" }` | session email not synced to Redmine |
| `404` | `{ "detail": "Parent issue not found" }` | `{issue_id}` doesn't exist |
| `403` | `{ "detail": "You are not a member of this issue's project" }` | PM/PC outside the project |
| `403` | `{ "detail": "You can only view subtasks of tickets you created or are assigned to" }` | TR accessing someone else's ticket |
| `429` | rate-limit payload | over 30 requests/minute |

### Access rules

- Admin: full access.
- Project Manager / Project Coordinator: must be a member of the parent issue's project; sees all subtasks.
- Technical Resource: must be assigned to or the author of the parent issue; sees only subtasks assigned to them or authored by them.

---

## 2. Ticket object fields

Every ticket returned by team-issues, my-issues/paginated, project issues,
and issue detail contains exactly these fields:

```
id: int
subject: string
description: string | null
status: string
priority: string
tracker: string
project_id: int
project_name: string
assigned_to_name: string | null
created_on: string (ISO datetime)
updated_on: string (ISO datetime)
start_date: string | null (YYYY-MM-DD)
due_date: string | null (YYYY-MM-DD)
estimated_hours: float | null
done_ratio: int
is_private: bool
status_id: int
priority_id: int
tracker_id: int
assigned_to_id: int | null
author_id: int
author_name: string
attachments: array of { id: int, filename: string, content_type: string, filesize: int, created_on: string }
custom_fields: array of { id: int, name: string, value: string }
parent_issue_id: int | null   (null = top-level ticket, number = subtask of that id)
subtasks_count: int           (0 = no subtasks)
```

---

## 3. Create ticket (with subtask support)

```
POST /api/redmine/issues
Content-Type: multipart/form-data
```

### Form fields

| Field | Type | Required | Rules |
|---|---|---|---|
| `data` | string (JSON) | yes | payload below; invalid JSON → `400 { "detail": "Invalid JSON in data field" }` |
| `files` | file(s) | no | max 2 files, max 5 MB each; extensions: `.jpg .jpeg .png .pdf .txt .csv .log .mp3 .wav .ogg .flac .aac .m4a .webm` |

### `data` JSON payload — all accepted keys

```json
{
  "project_id": 34,
  "subject": "Subtask subject here",
  "description": "Optional, max 65535 chars, can be null",
  "tracker_id": 1,
  "status_id": 1,
  "priority_id": 3,
  "assigned_to_id": 56,
  "category_id": null,
  "fixed_version_id": null,
  "parent_issue_id": 136,
  "start_date": "2026-09-09",
  "due_date": "2026-09-10",
  "estimated_hours": 4.5,
  "is_private": false,
  "watcher_user_ids": [21, 56],
  "custom_fields": [{ "id": 4, "value": "3rInfotech" }]
}
```

Rules: `project_id` (int, required), `subject` (required, 1–255 chars),
`start_date`/`due_date` must be `YYYY-MM-DD` with `due_date >= start_date`,
`estimated_hours >= 0`, each `custom_fields` item must be a dict with int
`id` and a `value` key. Violations → `422` with
`[{ "field": "<name>", "message": "<reason>" }]`.

### Success response `201`

```json
{ "status": "success", "message": "Issue created successfully", "id": 141 }
```

If `assigned_to_id` is someone other than the creator, the assignee also gets
a `ticket_assigned` in-app notification.

### Error responses

| Status | Body | Meaning |
|---|---|---|
| `403` | `{ "detail": "Access denied" }` | no role in session |
| `400` | `{ "detail": "Redmine user not found. Contact admin to sync your account." }` | session email not synced |
| `403` | `{ "detail": "You are not a member of this project" }` | non-admin outside the project |
| `429` | `{ "detail": "You can create up to 50 tickets per day. Please try again tomorrow." }` | daily create limit hit |
| `409` | `{ "detail": "An issue with this subject already exists in this project" }` | duplicate subject in project |
| `404` | `{ "detail": "Parent issue #136 not found" }` | parent doesn't exist |
| `400` | `{ "detail": "Subtask must belong to the same project as its parent issue #136" }` | parent in another project |
| `502` | `{ "detail": "Redmine error: <redmine body>" }` | Redmine itself failed |

Note: if `assigned_to_id` is not a project member, it is silently dropped
(ticket is created unassigned).

---

## 4. Update ticket (with subtask support)

```
PUT /api/redmine/issues/{issue_id}
Content-Type: multipart/form-data
```

Same form fields as create (`data` JSON + optional `files`).
Limits: max 20 updates per user per day → `429`; max 10 attachments per
ticket → `400`. Technical Resource users can only edit tickets they created
or are assigned to.

### `data` JSON payload — all accepted keys (every key optional)

```json
{
  "subject": "Updated subject, 1–255 chars",
  "description": "Updated description, max 65535 chars",
  "tracker_id": 1,
  "status_id": 2,
  "priority_id": 3,
  "assigned_to_id": 56,
  "category_id": null,
  "fixed_version_id": null,
  "parent_issue_id": 136,
  "start_date": "2026-09-09",
  "due_date": "2026-09-10",
  "estimated_hours": 4.5,
  "done_ratio": 50,
  "is_private": false,
  "watcher_user_ids": [21],
  "custom_fields": [{ "id": 4, "value": "3rInfotech" }],
  "notes": "Journal comment, max 10000 chars",
  "private_notes": false
}
```

Rules: `done_ratio` 0–100, same date/custom-field rules as create.
Violations → `422`.

### Success response `200`

```json
{ "status": "success", "message": "Issue updated successfully", "id": 141 }
```

### Parent-specific error responses

| Status | Body | Meaning |
|---|---|---|
| `400` | `{ "detail": "An issue cannot be a subtask of itself" }` | self-parenting |
| `404` | `{ "detail": "Parent issue #136 not found" }` | parent doesn't exist |
| `400` | `{ "detail": "Subtask must belong to the same project as its parent issue #136" }` | parent in another project |
