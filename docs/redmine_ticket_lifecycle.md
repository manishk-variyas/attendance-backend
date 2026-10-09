# Redmine Ticket Lifecycle & API Endpoints Guide

A simple, step-by-step guide explaining the lifecycle of a ticket and its corresponding API endpoints.

---

## Ticket Lifecycle Flow

```
[0. Load Options] ➔ [1. Create Ticket] ➔ [2. List / View] ➔ [3. Start Work] ➔ [4. Add File] ➔ [5. Resolve] ➔ [6. Close / Reopen]
                       (Status: New)                           (Status: In Progress)                (Status: Resolved)     (Status: Closed)
```

---

## Step 0: Load Dropdown Options (Form Setup)

Before displaying the ticket creation or edit form, load dropdown values:

- **Trackers** (Bug, Feature, Support): `GET /api/redmine/trackers`
- **Priorities** (Low, Normal, High P1, Immediate P0): `GET /api/redmine/priorities`
- **Statuses** (New, In Progress, Resolved, Closed, Rejected): `GET /api/redmine/statuses`
- **Assignees (Users)**: `GET /api/redmine/users`

---

## Step 1: Create Ticket (Status: `New` / ID 1)

When a user submits the "Create Ticket" form.

- **Endpoint**: `POST /api/redmine/issues`
- **Header**: `Content-Type: multipart/form-data`
- **Form Data**:
  - `data` (JSON string):
    ```json
    {
      "project_id": 34,
      "subject": "Login button disabled on mobile",
      "description": "User cannot tap login button on iOS devices.",
      "tracker_id": 1,
      "priority_id": 2,
      "status_id": 1,
      "assigned_to_id": 56,
      "due_date": "2026-10-15"
    }
    ```
  - `files`: Optional file object attachment

- **Response (`201 Created`)**:
  ```json
  {
    "status": "success",
    "message": "Issue created successfully",
    "id": 140
  }
  ```

---

## Step 2: List & View Tickets

### List Logged-In User's Tickets
- **Endpoint**: `GET /api/my-issues/paginated?page=1&page_size=25`
- **Response**: Paginated list of ticket objects (`records`, `total`, `page`, `page_size`).

### Get Single Ticket Details
- **Endpoint**: `GET /api/redmine/issues/{ticket_id}`
- **Response**: Complete ticket details including description, author, assignee, and attachments.

---

## Step 3: Start Working on Ticket (Status: `In Progress` / ID 2)

When the developer starts working, they update the status to **In Progress** (`status_id: 2`), update completion percentage (`done_ratio`), and add a progress comment (`notes`).

- **Endpoint**: `PUT /api/redmine/issues/{ticket_id}`
- **Header**: `Content-Type: multipart/form-data`
- **Form Data**:
  - `data` (JSON string):
    ```json
    {
      "status_id": 2,
      "done_ratio": 30,
      "notes": "Investigating button touch handler issue in mobile Safari."
    }
    ```

- **Response (`200 OK`)**:
  ```json
  {
    "status": "success",
    "message": "Issue updated successfully",
    "id": 140
  }
  ```

---

## Step 4: Attach Files or Audio Notes (Optional)

Upload additional logs, screenshots, or voice recordings while working.

- **Endpoint**: `POST /api/redmine/issues/{ticket_id}/attachments`
- **Header**: `Content-Type: multipart/form-data`
- **Form Data**: `file` (binary file)
- **Response (`200 OK`)**:
  ```json
  {
    "status": "success",
    "message": "Recording uploaded",
    "id": 140
  }
  ```

---

## Step 5: Resolve Ticket (Status: `Resolved` / ID 3)

When the developer completes the work and deploys the fix.

- **Endpoint**: `PUT /api/redmine/issues/{ticket_id}`
- **Header**: `Content-Type: multipart/form-data`
- **Form Data**:
  - `data` (JSON string):
    ```json
    {
      "status_id": 3,
      "done_ratio": 100,
      "notes": "Fixed touch event handler. Deployed to staging environment."
    }
    ```

---

## Step 6: Close, Reopen, or Reject Ticket

### Option A: Close Ticket (Status: `Closed` / ID 4)
PM / Tester verifies the fix and closes the ticket.
- **Endpoint**: `PUT /api/redmine/issues/{ticket_id}`
- **Data Payload**: `{"status_id": 4, "notes": "Verified fix on staging environment."}`

### Option B: Reopen Ticket (Status: `In Progress` / ID 2)
If testing fails, set status back to **In Progress**.
- **Endpoint**: `PUT /api/redmine/issues/{ticket_id}`
- **Data Payload**: `{"status_id": 2, "notes": "Reopened: Bug still reproduces on iPhone SE."}`

### Option C: Reject Ticket (Status: `Rejected` / ID 5)
If ticket is invalid or duplicate.
- **Endpoint**: `PUT /api/redmine/issues/{ticket_id}`
- **Data Payload**: `{"status_id": 5, "notes": "Rejected: Duplicate of ticket #120."}`

---

## Summary Reference Table

| Lifecycle Stage | HTTP Method & Endpoint | Payload / Key Fields |
|---|---|---|
| **0. Fetch Options** | `GET /api/redmine/{trackers\|priorities\|statuses\|users}` | None |
| **1. Create Ticket** | `POST /api/redmine/issues` | `data`: `{ project_id, subject, tracker_id, priority_id, status_id: 1, assigned_to_id }` |
| **2. List My Tickets** | `GET /api/my-issues/paginated` | Query params: `page`, `page_size`, `status_id`, `search` |
| **3. Ticket Details** | `GET /api/redmine/issues/{id}` | Path parameter `{id}` |
| **4. Start Work** | `PUT /api/redmine/issues/{id}` | `data`: `{ status_id: 2, done_ratio: 30, notes: "Comment" }` |
| **5. Add Attachment** | `POST /api/redmine/issues/{id}/attachments` | `file`: binary file |
| **6. Resolve Ticket** | `PUT /api/redmine/issues/{id}` | `data`: `{ status_id: 3, done_ratio: 100, notes: "Resolution note" }` |
| **7. Close Ticket** | `PUT /api/redmine/issues/{id}` | `data`: `{ status_id: 4, notes: "Closed" }` |
