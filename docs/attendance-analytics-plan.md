# Attendance Analytics — Plan & Algorithm

How the attendance analytics for a single person are computed. Written so
anyone (not just developers) can follow it.

---

## 1. Data we use

| Source | What it gives us |
|---|---|
| `shifts` | The person's **assigned schedule** (the roster) |
| `attendance` | The person's **punches** (present, WFH, leave, hours, late) |
| `leaves` | Approved leave requests |
| `holidays` | Company holidays |
| `employee_master` | Who is an active employee (and their login) |

---

## 2. The algorithm (simple steps)

**Step 1 — Get the schedule.**
For the chosen date range, list the days the person has a **shift**. These are
their **working days**. A day with no shift is *not* a working day (whether it's
a weekday, weekend, or holiday).

**Step 2 — Get the punches.**
Read the person's attendance rows for the range.

**Step 3 — Put each working day into a bucket.**

| Bucket | Meaning |
|---|---|
| **Present** | Punched in (office or home) |
| **WFH** | Punched in *from home* |
| **Leave** | Leave day (approved leave / leave status) |
| **Absent** | Working day with no punch and no leave |

**Step 4 — Add up.**
- `working_days` = number of shift days
- `present_days`, `wfh_days`, `leave_days`, `absent_days` = counts from Step 3
- `absent_days = max(working_days − present_days − leave_days, 0)`

**Step 5 — Percentage.**
```
attendance_pct = present_days ÷ working_days × 100
```
If there are no working days, the percentage is 0.

---

## 3. Punctuality (timing)

- **Late days** = days the punch-in was after `shift start + grace minutes`.
- **Check-ins** = days the person actually punched in.
- **Avg check-in** = average punch-in time (IST, `HH:MM`).
- **Earliest / latest check-in** = first and last punch-in in the range.
- **By weekday** = how many late arrivals fell on each weekday.
- **Trend** = late days ≥ the threshold (default 3).

---

## 4. Absenteeism (absence pattern) (Check against the employee data (Proof))

- **Longest streak** = longest run of consecutive **working days** with no
  attendance. Non-working days in between do **not** break a run.
- **Mon / Fri absences** = absences that fell on Mondays / Fridays.
- **Red-flag streak** = a run of `streak_min` (default 3) or more missed
  working days.
- **Zero attendance** = people with no attendance rows at all in the range.

---

## 5. Rules that matter

- **Working days come from the roster, not the calendar.** Only days with an
  assigned shift count — even if the shift is on a weekend or a holiday.
- **Team scope = active employees only.** Accounts with
  `employee_master.is_active = false` (test/service/inactive) are excluded.
- **Holidays** are shown separately (`holiday_days`); a shift on a holiday is
  both a working day and a holiday (shift is a holiday for others and but it is a working day for him) he gets a comp off or his leaves are adjusted against the unapproved leaves may be an extra day).
- **Timezone** is IST for all day boundaries.
- **Date range** is capped at 366 days.

---

## 6. Worked example — Vishal, September 2026

| Step | Result |
|---|---|
| Shift days (working days) | 20 |
| Present (all WFH) | 10 |
| Leave | 0 |
| Absent = 20 − 10 − 0 | 10 |
| Attendance % = 10 ÷ 20 | **50.0%** |
| Late days | 7 |
| Avg check-in | 11:57 |
| Longest absence streak | 9–18 Sep (7 working days) → red flag | *(Recheck the data and the algorithm)
| Mon / Fri absences | 0 / 3 |

---

## 7. Endpoints

| Endpoint | Who it's for |
|---|---|
| `GET /api/analytics/attendance/me` | The logged-in user |
| `GET /api/analytics/attendance/member` | One employee (self or lead in scope) |
| `GET /api/analytics/attendance/team` | Whole team (Admin/PM/PC) |
| `GET /api/analytics/attendance/punctuality/*` | Punctuality |
| `GET /api/analytics/attendance/absenteeism/*` | Absenteeism |
