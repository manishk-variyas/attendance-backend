# Auth & sessions

## Login
`POST /auth/login` → verifies credentials against **Keycloak**, creates a
server-side session row, sets a session cookie. Non-default realms require
`"realm": "<name>"` (e.g. `"vapt"`).

## Session enforcement
- Absolute TTL and idle timeout (`settings.SESSION_*`).
- `enforce_session_limit(sub, max_sessions=1)` runs on login — **a new login
  evicts the user's other active sessions**. Multiple devices/tabs will log each
  other out.
- Expiry/idle soft-delete the session (`is_active = false`), rows kept for audit.

## Rate limiting (slowapi)
- Keyed on the **real client IP** via `app/core/client_ip.py`
  (`X-Real-IP` / `X-Forwarded-For`, trusted because nginx is the only entry).
- Login is a coarse burst guard; precise brute force is per-account (below).
- Limiter store is **in-memory per gunicorn worker** (4 workers) — limits are
  approximate unless moved to shared storage (Redis).

## Brute-force protection (`app/utils/login_security.py`)
- **Per-(account, IP)** guard for login: 5 failures → block that pair (15 min).
  Never blocks an IP globally (shared NAT/proxy safe).
- **Per-account** guard: 10 failures → block account (30 min).
- IP key comes from `_get_security_ip` (trusted proxy headers).
- Successful login clears the pair + account failures.

## Keycloak ↔ Redmine passwords
Independent. Redmine uses a local hash. They only match when synced (the app's
`/reset-password` syncs; normal login does not). See `gotchas.md`.
