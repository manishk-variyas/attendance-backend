# Per-Tab Sessions — Plan & Backend Fix

## 1. The issue
Any two users in the same browser (different tabs) conflict:
- **Tab 2's login replaces Tab 1's user for all tabs** (last login wins).
- **Logout in one tab logs out every tab.**
- Tab 2 renders the *previous* user's options (stale cached state).

Not admin-specific — it's **any two users**.

## 2. Root cause
There is exactly **one `session_id` cookie per browser**, shared by every tab.
Auth lived only in that cookie, so there is one session per browser, not per tab.

## 3. The proper fix
Move the session out of the shared cookie into **per-tab storage** and let the
backend accept it via a header.

`sessionStorage` is **per-tab** — the primitive we need. Each tab holds its own
token → its own session.

### Design
- `POST /auth/login` returns **`session_token`** in the body (and still sets the
  cookie, for non-SPA/mobile clients).
- A shared helper resolves the session from the request:
  **`Authorization: Bearer <token>`** (or `X-Session-Id`) first → **cookie**
  fallback.
- Frontend stores the token in `sessionStorage` and sends the header.

## 4. Backend changes (implemented)
- **New `app/utils/session_token.py::get_request_session_id(request)`** — header
  first, cookie fallback.
- Used everywhere the session is read:
  - `features/auth/dependencies.py` (`get_current_user`)
  - `middleware/active_employee.py`
  - `features/auth/routes.py` (`/login` switch, `/refresh`, `/activity`, `/logout`)
- `POST /auth/login` and `POST /auth/refresh` now return **`session_token`**.

## 5. Result
- Tab 1 (user A) + Tab 2 (user B) → **independent** sessions.
- Logout in Tab 1 → only Tab 1 is logged out; Tab 2 unaffected.
- Existing cookie clients keep working unchanged (fallback).

## 6. Trade-off
`sessionStorage` is JS-readable, so an XSS could steal the token (an `HttpOnly`
cookie can't). Mitigate with a strict **CSP** and short session lifetime. This is
the standard SPA trade-off for per-tab sessions.

## 7. Rollout
1. Backend accepts header **and** cookie + returns `session_token` (done — nothing breaks).
2. Frontend switches to `sessionStorage` + `Authorization` header (see
   `per-tab-sessions-frontend.md`).
3. Optionally stop setting the cookie later.

## 8. Verified (live)
- Login → `session_token`.
- `/auth/me` via header only (no cookie) → correct user.
- Two tokens (admin + non-admin) both valid & independent.
- Logout B (header) → B `401`, **A still valid**.
- Cookie fallback still works; regression suites green.
