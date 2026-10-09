# How the Analytics Numbers Are Calculated

Plain formulas for each stat — use these as help text / tooltips in the UI.

Common idea: **working days come from the employee's shifts** (the roster). A day
is a working day only if the person has a shift that day. Weekends and holidays
without a shift are not working days.

---

## Attendance

| Stat | How it's calculated | In simple words |
|---|---|---|
| `working_days` | Count of days with a shift | Days you were scheduled |
| `present_days` | Attendance rows with `status = present` | Days you punched in (office or home) |
| `wfh_days` | Present rows with location = `WFH` | Days you worked from home |
| `office_days` | `present_days − wfh_days` | Days you worked from office |
| `leave_days` | Shift days covered by an **approved/emergency leave request** (or a leave-marked attendance row); a day you actually punched in stays present | Days you were on approved leave |
| `absent_days` | `max(working_days − present_days − leave_days, 0)` | Scheduled days with no punch and no leave |
| `late_days` | Attendance rows with `is_late = true` | Days you checked in late |
| `recorded_days` | Number of attendance rows | Days with any record |
| `total_hours` | Sum of `total_hours` | Total hours worked |
| `avg_hours_per_day` | `total_hours ÷ present_days` | Average hours per present day |
| `attendance_pct` | `present_days ÷ working_days × 100` | Share of scheduled days you attended |
| `holiday_days` | Holidays that apply to you in the range | Holidays for your state (+ national) |

> **Note:** `absent_days` counts only **scheduled (shift) days**. A holiday or
> weekend with no shift is never absent. A holiday **with** a shift that you
> didn't punch = absent.
>
> **Approved leave counts as leave, not absent.** If an approved/emergency leave
> request covers a shift day, it reduces `absent_days`. If you punched in that
> day anyway, **present wins** (it's not counted as leave too).

---

## Punctuality

| Stat | How it's calculated | In simple words |
|---|---|---|
| `late_days` | Attendance rows with `is_late = true` | Late check-ins |
| `total_checkins` | Rows with a check-in time | Days you checked in |
| `avg_check_in` | Average of check-in times (IST) | Your usual check-in time |
| `earliest_check_in` | Earliest check-in in range | Earliest you checked in |
| `latest_check_in` | Latest check-in in range | Latest you checked in |
| `by_weekday` | Late count per weekday | Which weekdays you're late most |
| `habitual` | `late_days ≥ threshold` (default 3) | Flag for repeat latecomers |

---

## Absenteeism

| Stat | How it's calculated | In simple words |
|---|---|---|
| `absent_days` | Scheduled shift days with no present/leave | Days you missed work |
| `longest_streak` | Longest run of consecutive absent working days | Your worst run of missed days |
| `streaks` | All absent runs ≥ 2 working days | Each block of missed days |
| `monday_absences` | Absences that fell on Monday | Missed Mondays |
| `friday_absences` | Absences that fell on Friday | Missed Fridays |
| `zero_attendance` | People with no attendance rows | No records at all |

> A run counts only **working days**; weekends/holidays in between don't break it.

---

## Tickets (self)

| Stat | How it's calculated | In simple words |
|---|---|---|
| `total` | Tickets assigned to me or created by me | All my tickets |
| `assigned_to_me` | Tickets currently assigned to me | On my plate |
| `created_by_me` | Tickets I authored | I created these |
| `open` | Not closed and not "Resolved" | Still open |
| `pending` | Status = `Resolved` | Done, awaiting close |
| `closed` | `is_closed = true` | Closed |
| `overdue` | Open and `due_date < today` (IST) | Past due |
| `open_now` / `overdue_now` | Same as `open`+`pending` / `overdue` | Current state |
| `created_in_range` | Tickets created in the range | New in period |
| `closed_in_range` | Tickets closed in the range | Closed in period |
| `authored_in_range` | Tickets I created in the range | Created by me in period |
| `avg_resolution_days` | Mean `closed_on − created_on` for closed tickets | Average time to close |
| `by_status` / `by_priority` / `by_tracker` | Count per status / priority / tracker | Breakdowns |

> Ticket header counts use the **current** state. Use `scope=range` to count only
> tickets created in the selected range.

---

## Leaves

| Stat | How it's calculated | In simple words |
|---|---|---|
| `applied` | Leave requests overlapping the range | Requests in period |
| `approved` / `pending` / `rejected` / `cancelled` | Count per status bucket | Status totals |
| `approved_days` | Approved-leave days that fall on your **shift days** | Working days you were on approved leave |
| `by_type` | Count per leave type | Leave-type split |
| `by_status` | Count per raw status | Status split |
| `trend` | Per day: applied / approved (by start date) | Daily activity |
| `monthly` | Last 6 months / the fetched window | Month-by-month |

---

## WFH

| Stat | How it's calculated | In simple words |
|---|---|---|
| `total` | WFH requests overlapping the range | Requests in period |
| `approved` / `pending` / `rejected` | Count per status | Status totals |
| `approved_days` | Approved WFH days on your **shift days** | Working days you were on approved WFH |
| `by_status` | Count per status | Status split |
| `trend` / `monthly` | Per day / per month | Activity over time |

---

## Team endpoints

- **Member rows** use each member's own numbers (their shifts, their state).
- **Team totals**: counts are summed; `absent_days` = sum of each member's absent.
- **`working_days` (team)** = distinct dates any team member has a shift.
- **`holiday_days` (team)** = union of holidays across members' states.

---

## Key rules to keep in mind

- **Working day = a day you have a shift.** Not "every weekday".
- **Absent = scheduled day, no punch, no leave.** Holidays/weekends without a
  shift are never absent.
- **Present includes WFH.** Office = present − WFH.
- **`approved_days`** counts only days that are also your shift days.
- **Day boundaries are IST.**
