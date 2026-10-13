# Per-Tab Sessions — Frontend Integration

Fix for "two users in one browser conflict" (last login wins for all tabs; logout
logs out all tabs). The backend now supports a **per-tab session token**; the
frontend must switch to it.

## 1. Login — store the token per tab
`POST /auth/login` now returns:
```json
{ "message": "Login successful", "user": {...}, "session_token": "…" }
```
```js
const { user, session_token } = await loginRes.json();
sessionStorage.setItem("session_token", session_token);   // PER-TAB (not localStorage)
setAuthUser(user);
```

## 2. Send the token on every request
```js
const token = sessionStorage.getItem("session_token");
fetch(url, {
  credentials: "include",               // keep for cookie fallback
  headers: token ? { Authorization: `Bearer ${token}` } : {},
  ...rest,
});
```
- Use **`sessionStorage`**, never `localStorage` (localStorage is shared across
  tabs — the original bug).
- A global fetch wrapper/interceptor is the cleanest place.

## 3. Refresh
`POST /auth/refresh` also returns a new `session_token` — **replace** the stored
one with it.

## 4. Logout — clear only this tab
```js
await fetch("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
sessionStorage.removeItem("session_token");
clearAuthState();
redirectToLogin();
```
This logs out **only the current tab**; other tabs keep their own tokens.

## 5. On 401
Clear `sessionStorage` + auth state, redirect to login (another tab may have
revoked this session, or it expired).

## 6. Per-tab user state
Keep the cached user/roles in `sessionStorage` too (per tab) and refresh from
`GET /auth/me` on load/focus. Gate admin UI on freshly-fetched roles.

## 7. What this fixes
| Before | After |
|---|---|
| Tab2 login replaces Tab1's user | Each tab has its own session |
| Logout logs out all tabs | Logout affects only the tab |
| Wrong user's options shown | Each tab shows its own user |

## 8. Notes
- **Cookie fallback remains**: clients that don't send the header still work
  (single session per browser). The SPA should prefer the header.
- Security: `sessionStorage` is JS-readable (XSS). Keep a strict CSP; sessions
  are short-lived. (Same trade-off as any token-in-storage SPA.)
- Two users at once now genuinely work in two tabs of the same browser.
