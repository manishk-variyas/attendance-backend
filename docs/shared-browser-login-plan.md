# Shared-Browser Login Issue — Simple Plan

## 1. What was the problem
If two people log in using the **same browser** (two tabs):

- The **second person to log in takes over**. Both tabs now show the second
  person (the first person is gone).
- When **one person logs out, both get logged out**.
- Sometimes a tab shows the **wrong user's options** (e.g. the previous user's).

This happened for **any two users**, not only admin vs normal.

## 2. Why it happened
The app kept the login in a **cookie**, and a cookie is **shared by every tab**
in the same browser.

So there was only **one login per browser**, not one per tab. The second login
overwrote the first, and logout cleared that one cookie (so every tab logged out).

The browser can never keep two different logins in cookies — that's how cookies
work.

## 3. The fix (the plan)
Give **each tab its own login**, using `sessionStorage` (which is **per tab**),
instead of the shared cookie.

**Backend (done):**
1. Login and refresh now also return a **`session_token`** in the response.
2. The backend accepts the login from an **`Authorization: Bearer <token>`**
   header first, and only falls back to the cookie if the header is missing.
3. Logout deletes **only that token's** session (other tabs stay logged in).
4. Old cookie login still works, so nothing breaks for mobile/old clients.

**Frontend (still to do):**
1. On login, save `session_token` in **`sessionStorage`** (per tab, not
   localStorage).
2. Send `Authorization: Bearer <token>` on **every** request.
3. On refresh, replace the saved token with the new one.
4. On logout, clear **only this tab's** token.
5. On `401`, clear the token and go to the login page.
6. Show the user/roles from a fresh `GET /auth/me` (not from a shared cache).

## 4. What changes after the fix
| Before | After |
|---|---|
| Second login replaces the first for all tabs | Each tab has its own login |
| Logout logs out all tabs | Logout affects only one tab |
| A tab shows the wrong user | Each tab shows its own user |
| Two users impossible in one browser | Two users work in two tabs |

## 5. How we verified (backend)
- Logged in `shubham@…` and `himanshu@…` in the same browser → both stayed
  logged in, each saw **their own** profile and data.
- Logged out one → the other **stayed** logged in.
- Admin vs non-admin: each token got its own permissions.
- Old cookie login still works.

## 6. Plan / steps to finish
1. **Backend** — done and tested (returns token, header first, logout per token,
   cookie fallback). ✅
2. **Frontend** — switch to `sessionStorage` + `Authorization` header
   (see `per-tab-sessions-frontend.md`). ⏳
3. **Test** — two users in two tabs; logout one; refresh; check each tab shows
   its own data.
4. **Tell users** — one browser can now run two logins in two tabs; for a fully
   separate login use a separate profile/incognito.

## 7. One trade-off (simple)
`sessionStorage` can be read by JavaScript, so a script bug (XSS) could steal the
token. To reduce risk we keep a strict CSP and short sessions. This is the normal
trade-off for per-tab logins.
