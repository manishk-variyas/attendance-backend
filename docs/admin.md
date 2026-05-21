# Admin Quick Management Guide

This guide provides step-by-step instructions for managing leaves and projects directly via the CLI.

---

## 1. Approving Leaves (Step-by-Step)

Leaves are stored in MongoDB using the user's Keycloak ID. Follow these steps to approve them:

### Step A: Find the User's ID
Run this command to find the internal ID for the user's email or username:
```bash
docker exec infra-keycloak-db-1 psql -U keycloak -d keycloak -t -A -c "SELECT id FROM user_entity WHERE email = 'user@example.com' OR username = 'tr001';"
```
*Copy the resulting UUID (e.g., `cbfcd303-7b37-4daf-8769-1760367f60f0`).*

### Step B: Approve the Leaves in MongoDB
Use the ID from Step A in the command below:
```bash
docker exec mongodb mongosh -u admin -p adminpassword --authenticationDatabase admin attendance_db --eval "db.leaves.updateMany({user_id: 'PASTE_ID_HERE', status: 'pending'}, {\$set: {status: 'approved', updated_at: new Date()}})"
```

---

## 2. Interactive MongoDB Shell (mongosh)

If you want to browse data manually:

1. **Enter the shell**:
   ```bash
   docker exec -it mongodb mongosh -u admin -p adminpassword --authenticationDatabase admin
   ```
2. **Select Database**:
   ```javascript
   use attendance_db
   ```
3. **List all pending leaves**:
   ```javascript
   db.leaves.find({ status: "pending" })
   ```
4. **Exit**: Type `exit` or press `Ctrl+D`.

---

## 3. Redmine Project & User Management

### Step A: Reset a User's Password
If a user is locked out or forgot their password:
```bash
docker exec infra-redmine-1 rails runner "user = User.find_by_login('tr001'); user.password = 'NEW_PASSWORD_HERE'; user.save!"
```

### Step B: List All Active Projects
To see what projects exist and their internal IDs:
```bash
docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "SELECT id, name, identifier FROM projects WHERE status = 1;"
```

### Step C: Assign a User to a Project
1. **Get User ID**:
   ```bash
   docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "SELECT id FROM users WHERE login = 'tr001';"
   ```
2. **Assign Membership** (Replace `PROJECT_ID` and `USER_ID`):
   ```bash
   docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "INSERT INTO members (project_id, user_id, created_on) VALUES (PROJECT_ID, USER_ID, NOW());"
   ```
3. **Assign Role** (Technical Resource is ID 10):
   ```bash
   # Find the member ID first
   docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "SELECT id FROM members WHERE user_id = USER_ID AND project_id = PROJECT_ID;"
   # Insert the role
   docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "INSERT INTO member_roles (member_id, role_id) VALUES (MEMBER_ID, 10);"
   ```

### Step D: Manage Project Issues
- **List all issues for a project**:
  ```bash
  docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "SELECT id, subject, assigned_to_id FROM issues WHERE project_id = PROJECT_ID;"
  ```
- **Reassign an issue**:
  ```bash
  docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "UPDATE issues SET assigned_to_id = NEW_USER_ID WHERE id = ISSUE_ID;"
  ```

---

## 4. Useful Reference IDs

### Redmine Roles
| Role Name | ID |
| :--- | :--- |
| Manager | 3 |
| Developer | 4 |
| Project Coordinator | 9 |
| Technical Resource | 10 |

### Leave Statuses
- `pending`
- `approved`
- `rejected`







docker exec -it mongodb mongosh -u admin -p adminpassword --authenticationDatabase admin
Current Mongosh Log ID: 6a05fbe0d45d9c3ba444ba88
Connecting to:          mongodb://<credentials>@127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&authSource=admin&appName=mongosh+2.8.2
Using MongoDB:          7.0.32
Using Mongosh:          2.8.2

For mongosh info see: https://www.mongodb.com/docs/mongodb-shell/


To help improve our products, anonymous usage data is collected and sent to MongoDB periodically (https://www.mongodb.com/legal/privacy-policy).
You can opt-out by running the disableTelemetry() command.

------
   The server generated these startup warnings when booting
   2026-05-08T05:39:22.296+00:00: Using the XFS filesystem is strongly recommended with the WiredTiger storage engine. See http://dochub.mongodb.org/core/prodnotes-filesystem
   2026-05-08T05:39:24.548+00:00: Soft rlimits for open file descriptors too low
------

test> use attendance_db
switched to db attendance_db
attendance_db> db.leaves.find({status:"pending"})
[
  {
    _id: ObjectId('6a01809fdb43b1ec820432b3'),
    user_id: '91086c71-80c6-45a0-acd0-a2068691b654',
    start_date: ISODate('2026-05-12T00:00:00.000Z'),
    end_date: ISODate('2026-05-12T00:00:00.000Z'),
    leave_type: 'UPL',
    reason: 'Applying from mobile leaves dashboard',
    status: 'pending',
    created_at: ISODate('2026-05-11T07:09:19.457Z')
  },
  {
    _id: ObjectId('6a0181c6db43b1ec820432b4'),
    user_id: '91086c71-80c6-45a0-acd0-a2068691b654',
    start_date: ISODate('2026-05-21T00:00:00.000Z'),
    end_date: ISODate('2026-06-27T00:00:00.000Z'),
    leave_type: 'UPL',
    reason: 'idk just let me \n',
    status: 'pending',
    created_at: ISODate('2026-05-11T07:14:14.922Z')
  },
  {
    _id: ObjectId('6a01bde9a2ba1303476011e2'),
    user_id: '71194c01-b977-4f59-ba08-372ae38d8daf',
    start_date: ISODate('2026-05-12T00:00:00.000Z'),
    end_date: ISODate('2026-05-28T00:00:00.000Z'),
    leave_type: 'UPL',
    reason: "how are you there bro i'm fine",
    status: 'pending',
    created_at: ISODate('2026-05-11T11:30:49.489Z')
  }
]
attendance_db> 
(To exit, press Ctrl+C again or Ctrl+D or type .exit)
attendance_db> 
app-backend@attendance-app-backend:~/backend-monorepo/infra$ ^C
app-backend@attendance-app-backend:~/backend-monorepo/infra$ docker exec infra-redmine-db-1 psql -U redmine -d redmine -c "SELECT id, login FROM users WHERE login = 'tr001';"
 id | login 
----+-------
 19 | tr001
(1 row)




To Approve - // Run this inside mongosh after 'use attendance_db'
db.leaves.updateOne(
  { 
    user_id: "cbfcd303-7b37-4daf-8769-1760367f60f0", 
    reason: "How are you" 
  }, 
  { 
    $set: { status: "approved", updated_at: new Date() } 
  }
)
