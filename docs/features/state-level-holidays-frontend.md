# Frontend Changes — State-Level Holidays

What the frontend needs to add so employees/offices can be linked to a **state**,
and holidays can be scoped by state.

The `state_code` field uses a code from the states API, e.g. `IN-UP`
(Uttar Pradesh). Show the **name** to users, store the **code**.

---

## 1. Get the state list (for every dropdown)

```
GET /api/states/options?country=IN
```

Response:

```json
[
  { "code": "IN-UP", "name": "Uttar Pradesh", "short_code": "UP" },
  { "code": "IN-MH", "name": "Maharashtra", "short_code": "MH" }
]
```

- Use this to fill state `<select>` inputs.
- Cache it (it rarely changes).

Full list (with `gst_code`, `type`, `is_active`) is at `GET /api/states`.

---

## 2. Onboarding / Create employee

**Add a State field** to the create-employee form (required for correct holidays).

Endpoint: `POST /api/employees`

Add to the payload:

```json
{
  "first_name": "Ravi",
  "last_name": "Kumar",
  "user_email": "ravi@example.com",
  "country": "India",
  "state_code": "IN-UP",
  "location_id": "<office uuid>"
}
```

- `state_code` is optional but recommended. If left blank, the employee uses
  their **office's** state.
- If the code is wrong, the API returns `400`
  `"Unknown state code '...'. Use a code from /api/states."`

---

## 3. Edit employee

Endpoint: `PUT /api/employees/{employee_id}`

Add `state_code` to the edit form and send it only when changed:

```json
{ "state_code": "IN-MH" }
```

---

## 4. Show state on employee list / detail

`GET /api/employees/v1` (list) and `GET /api/employees/{id}` return **both**
`state_code` and `state_name` on each employee:

```json
{ "state_code": "IN-UP", "state_name": "Uttar Pradesh" }
```

- Display `state_name` directly (no mapping needed). Fall back to `state_code`
  if `state_name` is null.
- Create / update / status / location responses also include `state_name`.
- `GET /api/employees/me` (self profile) does **not** include state yet — use the
  employee record if you need it.

---

## 5. Office location form

**Add a State field** to create/edit office location.

- Create: `POST /api/admin/office-locations`
- Update: `PUT /api/admin/office-locations/{id}`

Payload field:

```json
{ "name": "Noida Office", "city": "Noida", "state_code": "IN-UP", "latitude": 28.5, "longitude": 77.4 }
```

- `GET /api/admin/office-locations` (list) and `GET /api/admin/office-locations/{id}`
  return **both** `state_code` and `state_name` for each office; show
  `state_name` directly. Create/update responses include both too.
- Wrong code → `400`.

> Tip: employees inherit their office's state, so setting the office state once
> covers everyone at that office.

---

## 6. Holiday upload / list

- The holiday Excel template already has a **Region** column. That column now
  means the **state code** (`IN-UP`) — blank = all states (national/country-wide).
- Uploading an unknown region returns `400`.
- `GET /api/leaves/holidays` returns each holiday with **`region`** (state code)
  and **`state_name`** (display name). Blank `region` / null `state_name` = all
  states. Show `state_name` (fall back to "All states").
- Filter the list by state with **`?region=IN-UP`** (new query param).
- The Excel export now has a **State Code** and **State** column.

---

## 7. Display helper

Keep one helper:

```
stateName(code) -> name   // from the states list; null/blank => "All states"
```

Use it on: employee list/detail, office list, holiday list, roster holiday labels.

---

## Field reference

| Field | Where | Type | Notes |
|---|---|---|---|
| `state_code` | employee create/update/response, office create/update/response | string \| null | e.g. `IN-UP`; from `/api/states` |
| `state_name` | employee response, office response | string \| null | display name, e.g. `Uttar Pradesh` |
| `region` | holiday create/list | string \| null | state code; blank = all states |
| `state_name` | holiday list | string \| null | display name; null = all states |

## Error to handle
- `400` `"Unknown state code '...'. Use a code from /api/states."` — show it on
  the form.

## Order of work (suggested)
1. Add the states list fetch + `stateName` helper.
2. Onboarding/create employee → State field.
3. Edit employee → State field.
4. Office create/edit → State field.
5. Holiday upload/list → Region = state + filter.
