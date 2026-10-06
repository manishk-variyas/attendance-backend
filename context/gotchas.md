# Gotchas

Read before debugging.

1. **Working days = shifts, not weekdays.** A weekday with no shift is not a
   working day. Do not reintroduce a Mon–Fri base (see `analytics.md`).

2. **Postgres `EXTRACT(DOW)`**: `0=Sun, 1=Mon … 5=Fri, 6=Sat`. Weekends are
   `IN (0,6)`. `>= 5` wrongly includes **Friday**.

3. **Duplicate `shifts` rows** exist (same user+date). Always
   `COUNT(DISTINCT s.date)`, never `COUNT(*)`.

4. **Keycloak vs Redmine passwords drift.** Redmine is a separate local
   password (`SHA1(salt + SHA1(pw))`). Changing Keycloak does not update
   Redmine. Sync only happens via `/reset-password`.

5. **Session limit = 1.** Logging in anywhere kills other sessions — looks like
   "random logouts". Many test logins as the same user cause this.

6. **Rate limiting is per real IP** via trusted proxy headers. The backend is
   only reachable through nginx; trusting `X-Real-IP` is safe here. Do not key
   security on the TCP peer (it's the shared proxy IP).

7. **In-memory limiter, 4 gunicorn workers** ⇒ effective limits ≈ 4×.

8. **pgbouncer is transaction-pooled.** Don't rely on session state; pin
   timezone explicitly in SQL (`AT TIME ZONE 'Asia/Kolkata'`).

9. **Analytics scope = active employees only.** Members without
   `employee_master` / `is_active=false` are excluded.

10. **`attendance.status` is an enum** (`attendance_status`); the ORM must use
    `SAEnum`, not `String` (writing with `::VARCHAR` fails).

11. **Redmine users resolve by email.** If Keycloak and Redmine emails differ,
    the user gets no projects (see `auth-and-sessions.md`).
