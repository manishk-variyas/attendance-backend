# Shift Management API - Full CRUD Guide

This guide provides the `curl` commands and payloads for performing Full CRUD operations on the Shift Management API.

## 0. Authentication (Get Session Cookie)

Before running any commands, you must log in to get a session cookie. This cookie handles authentication for all subsequent requests.

```bash
# Log in and save cookie to cookie.txt
curl -s -c cookie.txt -X POST "http://localhost/auth/login?username=tr001&password=tr001@123"
```

---

## 1. Create Shift (POST)

Creates a new shift record.

*   **TR (Technical Resource):** Can only create for themselves.
*   **PC / PM / Admin:** Can create for users in projects they manage.

```bash
curl -s -b cookie.txt -X POST http://localhost/api/shifts \
-H "Content-Type: application/json" \
-d '{
    "userId": 19,
    "userName": "tr001",
    "userEmail": "manishk@variyaslabs.com",
    "projectId": 2,
    "projectName": "DevOps_Noida",
    "shift": "morning",
    "workStatus": "OFFICE",
    "workAddress": "HQ 1st Floor",
    "date": "2026-05-18",
    "shiftStartTime": "09:00",
    "shiftEndTime": "17:00"
}'
```

---

## 2. Bulk Create Shifts (POST)

Creates one shift per day for a date range.

*   **PC / PM / Admin only.**

```bash
curl -s -b cookie.txt -X POST http://localhost/api/shifts/bulk \
-H "Content-Type: application/json" \
-d '{
    "userId": 19,
    "userName": "tr001",
    "userEmail": "manishk@variyaslabs.com",
    "projectId": 2,
    "projectName": "DevOps_Noida",
    "shift": "general",
    "workStatus": "WFH",
    "startDate": "2026-05-20",
    "endDate": "2026-05-22",
    "shiftStartTime": "09:00",
    "shiftEndTime": "17:00"
}'
```

---

## 3. Read Shift Operations (GET)

### Get Current User's Shift History

```bash
curl -s -b cookie.txt "http://localhost/api/shifts/history?limit=10&skip=0"
```

### Get Most Recent Shift for a Specific User

*   **Admin / PM / PC** (or the user themselves requesting their own ID).

```bash
# Replace 19 with the target user's Redmine ID
curl -s -b cookie.txt http://localhost/api/shifts/current/19
```

### Get Active Shift for a User

```bash
curl -s -b cookie.txt http://localhost/api/shifts/active/19
```

---

## 4. Update Shift (PUT)

Updates an existing shift.

*   **Admin / PM / PC / TR** can update (subject to project access checks).

```bash
# Replace SHIFT_ID_HERE with the actual MongoDB _id returned from creation
curl -s -b cookie.txt -X PUT http://localhost/api/shifts/SHIFT_ID_HERE \
-H "Content-Type: application/json" \
-d '{
    "workStatus": "WFH",
    "shift": "night",
    "shiftStartTime": "18:00",
    "shiftEndTime": "02:00"
}'
```

---

## 5. Delete Shift (DELETE)

Deletes a shift by ID.

*   **Admin Only** (by default in current code).

```bash
curl -s -b cookie.txt -X DELETE http://localhost/api/shifts/SHIFT_ID_HERE
```
