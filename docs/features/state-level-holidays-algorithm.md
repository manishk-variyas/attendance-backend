# State-Level Holidays Management — How It Works 



---

## 1. What this feature does

Before: every holiday applied to **everyone**.

Now: a holiday applies **only to the states it belongs to**. National holidays
still apply to everyone.

---

## 2. The main rule (simple)

A holiday applies to an employee when **any** of these is true:

1. It is a **national** holiday (`is_national = true`), **or**
2. It is a **country-wide** holiday (no state set, `region` is empty), **or**
3. Its **state** is the **same as the employee's state**.

In short:

```
applies  =  is_national  OR  region is empty  OR  region == employee_state
```

---

## 3. How we know an employee's state

Each employee has a state. We find it like this:

1. If the employee has their own state → use it.
2. Otherwise → use the state of the **office** they are linked to.

```
employee_state = employee.state_code  OR  office.state_code
```

So setting the office's state once covers everyone at that office.

---


## 4. The algorithm (step by step) 

1. **Keep a master list of states.** One table (`states`) holds all state codes
   and names (e.g. `IN-UP` = Uttar Pradesh).
2. **Give each office a state**, and each employee a state (from their office,
   or their own if set).
3. **Give each holiday a state** (`region` = state code). Empty = all states.
4. **When we need the holidays for a person:**
   - Find the person's state.
   - Pick holidays where `is_national = true` **OR** `region` is empty **OR**
     `region` equals the person's state.
5. **Use that holiday list** for the holiday count, the roster, leave checks,
   shift/WFH creation, and comp-off.

---

## 5. Worked example

Holidays: Sep 4 (country-wide), Sep 15 (UP only), Sep 16 (TS only).

| Employee | State | Holidays they see |
|---|---|---|
| Manish | IN-UP (Uttar Pradesh) | Sep 4, **Sep 15** (not Sep 16) |
| Sales | IN-TS (Telangana) | Sep 4, **Sep 16** (not Sep 15) |

Both see the country-wide holiday; each sees only their own state holiday.

---

## 6. What we changed

**New table**
- `states` — master list of states (code, name, short code, GST code, type).
  Seeded with India's 28 states + 8 union territories.

**New columns**
- `office_locations.state_code` — the office's state.
- `employee_master.state_code` — the employee's state (optional override).
- `holidays.region` — now means the **state code** (was unused). Empty = all.
- We also linked these to the `states` table (foreign keys) and added timestamps.

**Backfill**
- Each office was given a state based on its city (e.g. Noida → IN-UP).
- Employees inherited their office's state.

**One shared helper** (`app/utils/holiday_scope.py`)
- `employee_state` — finds a person's state.
- `holiday_scope_sql` — builds the "does it apply?" rule.
- `find_holiday` — the holiday for a person on a date.
- `holiday_dates_in_range` — holidays for a person in a date range.

**Places we plugged the rule in (so everything agrees)**
1. Attendance analytics — holiday count.
2. Shift roster — holiday marks.
3. Shift creation — skip holidays.
4. Shift warnings.
5. WFH creation — skip holidays.
6. Comp-off when someone works a holiday.
7. Leave validation — is this day a holiday.
8. Holiday upload — `region` must be a valid state code.
9. Holiday list — shows `region` + `state_name`, and can filter by state.

**Admin API for states** (`/api/states`)
- Read for everyone; **create/edit/delete only for Admin**.
- Delete is a soft delete (keeps history).

---

## 7. Simple rules to remember

- Working days still come from **shifts** — this feature does not change that.
- A holiday with **no state** applies to **everyone** in that country.
- A holiday with a **state** applies only to people in that state.
- If a person has no state, they still get national + country-wide holidays.
- `region` = the state **code**; `state_name` = the state **name** for display.

---

## 8. Edge cases handled

- **Unknown state code** on save/upload → clear `400` error (no crash).
- **Duplicate holidays** → blocked by a unique rule (country + state + date + name).
- **Old bad data** (`region = 'IN'`) → cleaned to empty.
- **Deleting a state** → soft delete, so old holidays/employees keep working.
- **Past date ranges** → unchanged (same result as before).



-- Testing api basd on the few condition - 

1 - Change the employee stae
2 - now check the roster calendar if it is hsowing the pemployee state based holidays or not


change state to - bihar


 

REquirement - State lvel holiday - they should be per state + national
Changes I need to do - 
Adapt the roster calendar basd on the state level holidays
Adapt the office location based on the state level
 - Fix the roster to show employees state specific holidays + national holiday 
 

 Throughput
Reword - Tickets created vs closed · -18% vs prioro
Fix the posisiton of the search and filter same like https://attendance.variyaslabs.com/attendance page (Employee Attendance)a






