# Roster System Documentation

## Overview

The attendance app has a roster (shift scheduling) system that allows administrators to assign shifts to employees. The system uses **Redmine as the source of truth for users and projects**, while **MongoDB** stores all roster/shift data.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (React)                             │
│                      http://localhost:5173                          │
│                         /roaster route                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ↓                    ↓                    ↓
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │  Redmine     │    │  Backend     │    │  Backend    │
   │  (External)  │    │  API         │    │  API        │
   │              │    │              │    │              │
   │ 65.1.71.177  │    │ 13.202.24.78 │    │ 13.202.24.78 │
   │   :3000      │    │   :5000      │    │   :5000      │
   └──────────────┘    └──────────────┘    └──────────────┘
        ↓                    ↓                    ↓
   Users/Projects       Shifts CRUD         Shifts Query
   (Read-only)        (MongoDB)            (MongoDB)
```

---

## API Endpoints

### Redmine APIs (via Vite Proxy)

The frontend proxies requests to Redmine at `http://65.1.71.177:3000`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/redmine-api/users.json` | GET | Fetch all users |
| `/redmine-api/users/:id.json?include=memberships` | GET | Get user's projects/roles |
| `/redmine-api/projects/:id.json` | GET | Get project details (custom fields) |

**API Key:** `7744af5a9c70fa0cf490e81f2e7f13592e7d7b5c`

### Backend APIs

Base URL: `http://13.202.24.78:5000`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/shifts` | POST | Create new shift |
| `/api/shifts/:id` | PUT | Update shift |
| `/api/shifts/:id` | DELETE | Delete shift |
| `/api/shifts/user/:userId` | GET | Get all shifts for a user |
| `/api/shifts/user/email/:email` | GET | Get shifts by email |
| `/api/shifts/current/:userId` | GET | Get active shift (within time range) |
| `/api/shifts/stats` | GET | Get overall shift statistics |
| `/api/shifts/active/:userId` | GET | Get active or current rostered shift |
| `/api/shifts/server-time` | GET | Get server time |

---

## Data Models

### Shift (MongoDB - shifts collection)

```javascript
{
  _id: ObjectId,
  userId: Number,           // Redmine user ID
  userName: String,         // "John Doe"
  userEmail: String,        // "john@example.com"
  projectId: Number,        // Redmine project ID
  projectName: String,     // "Project ABC"
  workAddress: String,     // From Redmine custom field "work_address"
  shift: String,           // "morning" | "general" | "afternoon" | "night" | "Not in Any Shift"
  workStatus: String,      // "WFH" | "OFFICE" | "CUSTOMER_SITE" | "LEAVE"
  leaveType: String,       // "PL" | "UL" | "CL" | "EL" | "W O" (if LEAVE)
  date: String,            // "2026-05-12"
  shiftStartTime: String,  // "09:00"
  shiftEndTime: String,    // "17:00"
  shiftStartUTC: Date,     // Converted to UTC
  shiftEndUTC: Date,       // Converted to UTC
  createdAt: Date,
  updatedAt: Date
}
```

---

## How It Works

### 1. Admin Creates Roster

1. Admin navigates to `/roaster`
2. Selects an employee → fetches from Redmine `/users.json`
3. Selects a project → fetches from Redmine (includes `work_address` custom field)
4. Selects date(s) - single or range (cannot select past dates)
5. Selects shift type, work status, start/end times
6. Submits → POST to `/api/shifts`
7. Shift saved to MongoDB

### 2. User Clocks In

When user starts a shift via `/api/location/shift/start`:
1. System finds rostered shifts for that user on that date
2. Compares actual start time vs rostered start time
3. Calculates lateness: `lateByMinutes = (actualStart - rosterStart) / (1000 * 60)`

### 3. Redmine Integration (Read-Only)

Redmine is used to:
- Fetch employee list
- Fetch project assignments per employee
- Get project-specific `work_address` custom field
- Add voice recording comments to tickets (separate feature)

**Redmine is NOT used to store shifts - only MongoDB holds shift data.**

---

## Frontend Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | Login | Login page |
| `/dashboard` | Dashboard | Attendance overview |
| `/employee/:email` | EmployeeDetail | Individual employee stats |
| `/roaster` | RoasterCreate | Roster management (calendar view) |

---

## How Roster Data is Displayed (Calendar View)

The roster uses **react-big-calendar** library to display shifts in a calendar view.

### Calendar Setup (Line 4532 in RoasterCreate.jsx)

```javascript
<Calendar
  localizer={localizer}           // moment.js localizer
  events={shifts}                // Array of shift events
  startAccessor="start"          // Event start time property
  endAccessor="end"               // Event end time property
  eventPropGetter={eventStyleGetter}  // Color coding by workStatus
  components={{
    event: CustomEvent,           // Custom event rendering
    toolbar: CustomToolbar,        // Custom navigation toolbar
  }}
  view={view}                     // month/week/day view
  onView={setView}
  date={calendarDate}
  onNavigate={setCalendarDate}
  views={["month", "week", "day"]}
/>
```

### How Shifts are Fetched and Displayed

1. **Fetch Shifts**: When a user is selected, `fetchUserShifts(userId)` is called
   - API: `GET http://13.202.24.78:5000/api/shifts/user/${userId}`
   - Returns all shifts for that user from MongoDB

2. **Transform to Calendar Events**: Each shift is mapped to calendar event format:
   ```javascript
   const shiftEvents = res.data.map((s) => ({
     id: s._id,
     title: `${s.shift} (${s.workStatus})`,  // e.g., "morning (WFH)"
     start: new Date(`${s.date}T${s.shiftStartTime}`),
     end: new Date(`${s.date}T${s.shiftEndTime}`),
     userName: s.userName,
     userEmail: s.userEmail,
     projectName: s.projectName,
     workStatus: s.workStatus,
     leaveType: s.leaveType,
     shift: s.shift,
     shiftStartTime: s.shiftStartTime,
     shiftEndTime: s.shiftEndTime,
     projectId: s.projectId,
     workAddress: s.workAddress
   }));
   ```

3. **Color Coding by Work Status** (`eventStyleGetter` function):
   | Work Status | Color | Hex |
   |-------------|-------|-----|
   | WFH | Green | #10b981 |
   | OFFICE | Orange | #f59e0b |
   | CUSTOMER_SITE | Purple | #8b5cf6 |
   | LEAVE | Red | #ef4444 |
   | KNA | Cyan | #06b6d4 |

4. **Custom Event Component**: Shows detailed tooltip on hover with:
   - User name & email
   - Project name
   - Shift type & time
   - Work address
   - Work status & leave type
   - Lock icon for past day shifts (cannot edit/delete)

### Calendar Views

- **Month View**: Shows all shifts in monthly grid
- **Week View**: Shows shifts in weekly columns
- **Day View**: Shows shifts for a specific day

### Navigation

- Custom toolbar with month/year navigation
- View toggle buttons (month/week/day)
- "Today" button to jump to current date

---

## Key Files

| File | Description |
|------|-------------|
| `backend/routes/shift.js` | Shift CRUD API endpoints |
| `backend/model/shift.model.js` | Mongoose schema for shifts |
| `backend/routes/location.js` | Clock in/out with lateness calculation |
| `backend/routes/upload.js` | Redmine integration for users/projects |
| `attendance_webadmin/src/pages/RoasterCreate.jsx` | Roster UI (React) - Line ~4532 for Calendar |
| `attendance_webadmin/vite.config.js` | Proxy config for Redmine API |

---

## Features

- **Single & Bulk Creation**: Create shifts for single date or date range
- **Multi-User Mode**: Assign same shift to multiple employees at once
- **Shift Types**: morning, general, afternoon, night, Not in Any Shift
- **Work Status**: WFH, OFFICE, CUSTOMER_SITE, LEAVE
- **Leave Types**: PL (Paid Leave), UL (Unpaid Leave), CL (Compensatory Leave), EL (Earned Leave), W O (Week Off)
- **Past Date Restriction**: Cannot create/modify shifts for past dates
- **Duplicate Detection**: Prevents duplicate shifts for same employee on same date
- **Lateness Tracking**: Calculates how late user clocked in vs rostered time
- **Shift History**: Tracks actual clock-in/out times for each user

---

## Shift History System

When users clock in/out, the system tracks their actual work sessions in the `shiftHistory` array within the User document (not in the Shift collection).

### Data Stored in User.shiftHistory

```javascript
{
  startTime: Date,              // When user actually started
  endTime: Date,                // When user ended shift (null if active)
  rosterStartTime: Date,        // The rostered start time (from Shift)
  shiftDurationMinutes: Number, // Planned shift duration
  lateByMinutes: Number,        // How many minutes late
  isLate: Boolean,              // Whether late or not
  highlightLate: Boolean,       // Highlight in UI
  locationAtStart: {            // GPS location at start
    latitude: Number,
    longitude: Number
  },
  locationAtEnd: {              // GPS location at end
    latitude: Number,
    longitude: Number
  }
}
```

### How Shift History Works

1. **User clocks in** (`POST /api/location/shift/start`):
   - Finds matching rostered shift for today/yesterday
   - Calculates lateness vs rostered time
   - Creates new entry in `shiftHistory` with start time & location

2. **User clocks out** (`POST /api/location/shift/end`):
   - Finds active shift in `shiftHistory`
   - Updates `endTime` and `locationAtEnd`

3. **Fetch history** (`GET /api/location/shift/history/:email`):
   - Returns last 10 shifts
   - Calculates `workedMinutes` from actual start/end
   - Shows status (ACTIVE/COMPLETED)

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/location/shift/start` | POST | Clock in, create history entry |
| `/api/location/shift/end` | POST | Clock out, update history entry |
| `/api/location/shift/history/:email` | GET | Get last 10 shifts |
| `/api/location/shift/status/:email` | GET | Check if shift active |

### Frontend Display

Shift history is shown in **EmployeeDetail** page (`/employee/:email`):
- Lists past clock-in/out sessions
- Shows lateness, worked minutes
- Displays location coordinates
- Shows status (ACTIVE/COMPLETED)

**Who can view**: ANY logged-in user can view any employee's shift history by navigating to `/employee/any-email`

---

## Access Control - Who Can Do What

### Authentication

- **Login**: Uses **Keycloak** (OIDC) for authentication
  - Server: `KEYCLOAK_SERVER_URL/realms/KEYCLOAK_REALM`
  - Any valid Keycloak user can log in
- **Token Check**: `PrivateRoute` checks for valid `accessToken` in localStorage

### Access Matrix

| Action | Who Can Do It |
|--------|---------------|
| **Login to App** | Any valid Keycloak user |
| **View Dashboard** | Any logged-in user |
| **View Employee Details** | Any logged-in user |
| **View Shift History** | Any logged-in user (any employee's history) |
| **View Roster (/roaster)** | Any logged-in user |
| **Create Shift** | Any logged-in user |
| **Update Shift** | Any logged-in user |
| **Delete Shift** | Any logged-in user |
| **Clock In/Out** | Any user (via mobile app or API) |

### Current Implementation

**There is NO role-based access control (RBAC)** in this app. Any valid Keycloak user can:
- Access all pages (dashboard, roster, employee detail)
- Create/Update/Delete any shift
- View ANY employee's shift history (by knowing their email)

**Note**: This is a security concern - anyone can:
- View all employees' attendance history
- Modify any rostered shift
- See when employees clock in/out and their GPS locations

Consider adding role checks for restricting access.
