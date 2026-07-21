# Demo Plan — June 30, 2026

## Users

| Username | Password | Role |
|---|---|---|
| `admin-vishal` | `vishaladmin@123` | System Admin |
| `pm001` | `pm001@123` | Project Manager |
| `tr001` | `tr001@123` | Technical Resource |
| `surajmittal` | `surajmittal@123` | Technical Resource |

---

## Flow 1 — Admin

### Dashboard
1. Login: `admin-vishal` / `vishaladmin@123`
2. Check active projects listing from Redmine
3. Check team presence data — project and team members
4. Check issues listing is proper
5. Check recordings are shown
6. Search shift history for any user
7. Delete a recording

### Schedule
1. Create a shift for a user
2. View shift listing in calendar by project and user

### Attendance
1. Delete a user's attendance log → make new entry via Manual Entry
2. Onboard a new user:
   - Assign project, keycloak role, redmine role, leave balance, office location
3. Assign Redmine issues to the new user

### Office Locations
1. Create new office location
2. Check duplicate entry blocked
3. Update existing office location

### Shift Definitions
1. Create new shift definition
2. Verify listing

---

## Flow 2 — PM (pm001 / pm001@123)

1. Login as `pm001`
2. Assign shift to new employee (TR)
3. View pending leave requests → approve / reject
4. View team attendance and reports

---

## Flow 3 — TR (Employee)

### Login as new user (after onboard)

### Check Shift
1. Login with employee credentials
2. Check assigned shift is showing in Schedule

### Start Shift
1. Click Start Shift based on timing
2. **Too early** → `"your shift starts at XX:XX on YYYY-MM-DD"`
3. **On time** → compares location with office
   - Inside radius → OFFICE
   - Outside radius → WFH
4. **Offline:** switch off internet → saves locally → toggle back → syncs
5. **Double click:** click Start again → same record, no error

### Check Today
- Shows shift start/end time, remaining hours

### End Shift
- Click End Shift → total hours shown
- If holiday → auto adds 1 comp-off

### Recording
- Go to Recordings → Upload Audio
- Enter issue ID from Redmine
- Offline → saves locally → syncs when online

### Leaves
- Apply leave for a shift day
- If already on leave or emergency leave:

  → `"You already have an approved or pending leave application that overlaps with these dates."`

### Compare Redmine Data
- Open Incidents section → compare with Redmine issues

---

## Night Shift Bonus (if time — tr003 / tr003@123)

1. Try check-in early → blocked "shift starts at 22:00"
2. Check-in at 22:00 → accepted
3. After midnight → /today shows correct next-day end time (07:00)
4. Early checkout → flagged "Early leave"
