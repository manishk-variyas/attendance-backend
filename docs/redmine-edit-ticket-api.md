# Redmine — Edit Ticket API

## Endpoint

```
PUT /api/redmine/issues/{issue_id}
```

- **Auth:** session cookie (`get_current_user`)
- **Content-Type:** `multipart/form-data` (**not** `application/json`)
- **Roles:** Admin (full), Project Manager / Project Coordinator (own project only),
  Technical Resource (only tickets they created or are assigned to)

## Request

`multipart/form-data` with two parts:

| Part | Type | Required | Description |
|---|---|---|---|
| `data` | text (string) | yes | A **JSON string** matching the `IssueUpdate` schema below |
| `files` | file(s) | no | Attachments (max 10 total per ticket) |

### `data` JSON (IssueUpdate)

Only send the fields you want to change — `null`/omitted fields are ignored.

| Field | Type | Notes |
|---|---|---|
| `subject` | string | 1–255 chars |
| `description` | string | max 65535 |
| `tracker_id` | int | |
| `status_id` | int | |
| `priority_id` | int | |
| `assigned_to_id` | int | Redmine user id (must be a project member, else silently dropped) |
| `category_id` | int | |
| `fixed_version_id` | int | |
| `parent_issue_id` | int | must exist + be in the same project, and not the issue itself |
| `start_date` | string | `YYYY-MM-DD` |
| `due_date` | string | `YYYY-MM-DD` |
| `estimated_hours` | float | ≥ 0 |
| `done_ratio` | int | 0–100 |
| `is_private` | bool | |
| `watcher_user_ids` | int[] | |
| `custom_fields` | object[] | `[{ "id": <int>, "value": <any> }]` |
| `notes` | string | max 10000 (comment added to the ticket) |
| `private_notes` | bool | |

### Example (curl)

```bash
curl -X PUT "$BASE_URL/api/redmine/issues/1234" \
  -b cookies.txt \
  -F 'data={"status_id":2,"done_ratio":50,"assigned_to_id":56,"notes":"Working on it"}'
```

With an attachment:

```bash
curl -X PUT "$BASE_URL/api/redmine/issues/1234" \
  -b cookies.txt \
  -F 'data={"notes":"See attached screenshot"}' \
  -F 'files=@/path/screenshot.png'
```

## Response

**200 OK**

```json
{
  "status": "success",
  "message": "Issue updated successfully",
  "id": 1234
}
```

## Errors

| Status | Body / meaning |
|---|---|
| `400` | `Invalid JSON in data field` · `An issue cannot be a subtask of itself` · `Subtask must belong to the same project as its parent issue #...` · attachment limit reached |
| `403` | role not allowed · `You are not a member of this issue's project` · `You can only edit tickets you created or are assigned to` |
| `404` | `Issue not found` · `Parent issue #... not found` |
| `422` | validation errors: `[{ "field": "<name>", "message": "<reason>" }]` |
| `429` | `You can update up to 20 tickets per day. Please try again tomorrow.` |
| `502` | `Redmine error: <upstream body>` |

## Notes for the developer

- The body is **multipart**, so in JS use `FormData`:
  ```js
  const fd = new FormData();
  fd.append("data", JSON.stringify({ status_id: 2, done_ratio: 50 }));
  fd.append("files", fileInput.files[0]); // optional
  fetch(`/api/redmine/issues/${id}`, { method: "PUT", body: fd, credentials: "include" });
  ```
  Do **not** set `Content-Type` manually — the browser adds the multipart boundary.
- `assigned_to_id` is only forwarded if that user is a project member (or it's the
  current user); otherwise it is dropped and the field won't change.
- Rate limit: **20 ticket updates per user per day** (`429` after that).
- The same multipart pattern is used by create: `POST /api/redmine/issues`
  (`data` = `IssueCreate` JSON, which also requires `project_id` and `subject`).
