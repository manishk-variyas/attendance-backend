# Shared-Browser Sessions — Issue, Fix & Frontend Integration

## 1. The issue
Two users log in from **the same browser** (different tabs):
- Tab 1: admin logs in.
- Tab 2: normal user opens the app → **sees admin** (or vice versa).

## 2. Root cause
Auth is a single `session_id` cookie (`HttpOnly`, `Path=/`). Cookies are **shared
across all tabs** of the same browser/origin — there is exactly one.

- `get_current_user` identifies you **solely** from that cookie → session row →
  that session's user/roles. Server-side there is no cross-user mix-up.
- So tab 2 sends tab 1's admin cookie → backend **correctly** sees admin.
- The normal user never actually gets their own session in tab 2 (or the login
  is skipped as "already authenticated").
- After user B *does* log in, the cookie is overwritten → **both** tabs become
  B. You can never hold admin + normal simultaneously in one browser profile.

This is inherent to cookie auth, not a privilege bug.

## 3. Backend fix (implemented)
1. **`GET /auth/me`** — returns the session user `{sub, username, email, roles,
   realm}`. The frontend calls this on load to detect *who* the cookie belongs
   to.
2. **Invalidate the old session on user switch** (`POST /auth/login`): if the
   request presents a session cookie belonging to a **different** user, that old
   session is deleted (and a `session_switch` security event logged) before the
   new session is created. So a switch leaves no stale admin session behind.
   - Verified: after B logs in presenting A's cookie, A's session → `401`, B's
     cookie → B.

## 4. Frontend integration (required)
1. **On app load:** call `GET /auth/me`.
   - `401` → show login.
   - `200` with a user **different** from the cached one → **replace** auth state
     (never merge) and refetch; or show *"Logged in as X — Logout to switch users"*.
2. **Login page:** if `/auth/me` returns 200, don't auto-proceed as that user —
   offer explicit **Logout then login**, or confirm the switch.
3. **After login:** replace the stored user/roles entirely; refetch.
4. **After logout** (`POST /auth/logout` clears the cookie): wipe **all** cached
   auth state (memory + localStorage/sessionStorage) and redirect to login.
5. **Never trust a cached role** across tabs without revalidating via `/auth/me`.

## 5. User guidance
Two accounts at once = two cookie jars: separate browser profiles, incognito
windows, or different browsers. No backend change can distinguish tabs.

## 6. Test (verified)
- A logs in → `/auth/me` = A.
- B logs in presenting A's cookie → 200; `/auth/me` (new cookie) = B;
  old session_A → `401 Invalid or expired session`.
