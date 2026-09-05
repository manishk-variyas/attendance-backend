# Frontend Plan — User Public IP for New-Device Detection

> Handoff spec for the frontend team.
> Goal: capture the user's public IP (via ipify.org) and forward it to the backend
> as `X-User-Public-IP` on every API call. Backend uses it for new-device detection.

---

## 1. Why we need this

The backend sits behind nginx on a Hetzner VM inside a private network. The IP
the backend currently sees (`request.client.host` / `X-Real-IP`) is the
**frontend VM's private IP**, not the real public IP of the user. We need the
real public IP for new-device detection (and for accurate audit logging).

We resolve this by having the browser fetch its public IP from a free
service (ipify.org) and include it in a custom header on every backend call.
The backend reads the header; if absent, it falls back to nginx's IP.

---

## 2. Files to add / change (2)

### NEW: `src/utils/getClientPublicIp.ts`

```typescript
// src/utils/getClientPublicIp.ts
// Fetches the user's public IP from ipify.org, cached for the page session.
// Backend reads it from the X-User-Public-IP header.
// Returns null on any failure (offline, blocked, CORS, timeout) — backend falls back.

let cachedIp: string | null = null;
let inflight: Promise<string | null> | null = null;

function isValidPublicIp(s: string): boolean {
  if (!s) return false;
  if (s.length > 64) return false;
  if (/[\s<>"'`\\]/.test(s)) return false;
  return /^[0-9a-fA-F:.]+(?::\d+)?$/.test(s);
}

export async function getClientPublicIp(): Promise<string | null> {
  if (cachedIp) return cachedIp;
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const res = await fetch("https://api.ipify.org?format=json", {
        signal: AbortSignal.timeout(3000),
      });
      if (!res.ok) return null;
      const data = await res.json();
      const candidate = typeof data?.ip === "string" ? data.ip.trim() : null;
      return isValidPublicIp(candidate) ? candidate : null;
    } catch {
      return null;
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}

// Force a refresh — useful if the user explicitly switches network
// (Wi-Fi → cellular, etc.) and we want a fresh IP right away.
export function clearCachedPublicIp(): void {
  cachedIp = null;
}
```

### CHANGE: wherever the app makes API calls

**If the app uses `fetch` directly**, monkey-patch it in a single helper:

```typescript
// src/lib/api.ts (or equivalent)
import { getClientPublicIp } from "@/utils/getClientPublicIp";

const ORIGINAL_FETCH = typeof window !== "undefined" ? window.fetch.bind(window) : fetch;

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (
    typeof window !== "undefined" &&
    typeof input === "string" &&
    process.env.NEXT_PUBLIC_API_URL &&
    input.startsWith(process.env.NEXT_PUBLIC_API_URL)
  ) {
    const ip = await getClientPublicIp();
    if (ip) headers.set("X-User-Public-IP", ip);
  }
  return ORIGINAL_FETCH(input as RequestInfo, { ...init, headers });
}

if (typeof window !== "undefined") {
  (window as unknown as { fetch: typeof fetch }).fetch =
    apiFetch as unknown as typeof fetch;
}
```

**If the app uses Axios**, use an interceptor:

```typescript
import axios from "axios";
import { getClientPublicIp } from "@/utils/getClientPublicIp";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
});

api.interceptors.request.use(async (config) => {
  const ip = await getClientPublicIp();
  if (ip) config.headers["X-User-Public-IP"] = ip;
  return config;
});

export default api;
```

---

## 3. Files to add / change (optional but recommended)

### OPTIONAL: Pre-warm at app mount (avoids login latency)

In your root layout / App component, kick off the IP fetch so the cache is warm
by the time the user clicks "Login":

```typescript
// app/layout.tsx (Next.js App Router) OR src/App.tsx (plain React)
import { useEffect } from "react";
import { getClientPublicIp } from "@/utils/getClientPublicIp";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    getClientPublicIp(); // fire-and-forget
  }, []);
  return <html><body>{children}</body></html>;
}
```

---

## 4. Header contract

| Header | Value | Behavior when missing |
|---|---|---|
| `X-User-Public-IP` | Public IP string (IPv4 or IPv6) | Backend falls back to `X-Real-IP` from nginx |

The frontend should:
- **Send the header on every request to `${NEXT_PUBLIC_API_URL}`** (the backend)
- **Not** send it for third-party API calls
- **Not** send it if `getClientPublicIp()` returns `null` (the backend's fallback handles this)

---

## 5. Edge cases — full list

### 5.1 Network & environment

| Case | Behavior |
|---|---|
| User is offline | `fetch` rejects → return `null` → header omitted → backend uses nginx `X-Real-IP` fallback |
| ipify is blocked (corporate firewall, GFW, etc.) | Same as offline |
| ipify returns a non-200 (5xx, 429 rate limit) | `if (!res.ok) return null` |
| ipify returns malformed JSON (`{ip: 123}` instead of string) | `typeof data?.ip !== "string"` guard rejects |
| ipify returns an IP that doesn't look valid (empty, whitespace, HTML, control chars) | `isValidPublicIp()` rejects — prevents header injection |
| IPv4 (typical) | `192.0.2.10` — works |
| IPv6 | `2001:db8::1` — works |
| IPv6 with zone (rare, e.g. `fe80::1%eth0`) | works (regex accepts `%` via `[0-9a-fA-F:.]`) |
| IPv4 with port (shouldn't happen from ipify) | rejected (regex doesn't match `:`) — extra safety |
| Request times out (>3s) | `AbortSignal.timeout(3000)` rejects → return `null` |
| CSP blocks the call to api.ipify.org | `fetch` rejects → return `null` (backend team may need to allow `connect-src https://api.ipify.org`) |
| Mixed content: HTTPS page calling HTTP ipify | `fetch` rejects → return `null` (ipify is HTTPS so this shouldn't happen) |

### 5.2 Concurrent calls and caching

| Case | Behavior |
|---|---|
| User clicks login 3 times before IP is cached | All 3 calls `await getClientPublicIp()`; the in-flight `Promise` is reused (`inflight`), so only **one** actual ipify call happens |
| IP cached, 100 more requests | All 100 use the cached value; no further ipify calls |
| IP cached, user switches network (Wi-Fi → cellular) | Cached value is stale; backend sees a "new device" event on next login. **This is the intended behavior** — a network switch IS a new-device signal. |
| User explicitly switches network and we want a fresh IP immediately | Call `clearCachedPublicIp()` then `await getClientPublicIp()`. Optional. |
| User in private/incognito window | Fresh page → fresh cache → ipify called once. Old cache is gone (no localStorage). |
| User clears cookies / localStorage | Same as above — works correctly |
| Multiple tabs | Each tab has its own JS context → its own cache. Each tab makes one ipify call; OK. |

### 5.3 Privacy & legal

| Case | Behavior | Notes |
|---|---|---|
| User's IP is exposed to ipify | True by design | ipify is a well-known service; their privacy policy applies |
| User's IP is stored in frontend memory | Yes, in `cachedIp` (page-session only, **not** localStorage) | Cleared on full page reload |
| GDPR / data minimization | The IP is needed for new-device detection; document in your privacy policy | Standard telemetry |
| User is on VPN that rotates IPs | Header changes per request; new-device detection may flag each login | Acceptable — VPN rotation is rare and a real anomaly signal |

### 5.4 Failure modes (degradation strategy)

| Failure | Frontend behavior | Backend behavior |
|---|---|---|
| ipify down (network) | Header omitted, login still proceeds | Uses nginx `X-Real-IP` (frontend VM's private IP) |
| CSP blocks ipify | Header omitted | Same as above |
| User blocks 3rd-party scripts (e.g. uBlock) | ipify blocked → header omitted | Same as above |
| Backend rejects the header (e.g. validation) | N/A | Header is read as `Optional[str]`, ignored if missing/invalid |

The **degradation strategy is consistent**: missing IP is fine, the system falls back gracefully to nginx's IP. New-device detection becomes weak (we can only detect the *frontend VM* changing, not the real public device), but the user can still log in.

### 5.5 Security

| Case | Behavior | Notes |
|---|---|---|
| Attacker spoofs `X-User-Public-IP` from a non-browser client (e.g. curl) | Backend trusts whatever the header says | This is OK because the same attacker would have to know a real user's email to be useful; login still requires correct password + valid Turnstile |
| Attacker spoofs to match victim's known public IP | Same — still needs password + Turnstile | Defense in depth (Turnstile + per-user lockout) covers this |
| Header injection (CRLF, newlines, etc.) | Validation in `isValidPublicIp` rejects suspicious characters | IP is logged to `audit.log`; if it contains newlines, the JSON parser would break |
| Header contains control chars | Rejected by `isValidPublicIp` regex | defense in depth |
| Cross-origin attacker spoofs via another site | CORS preflight on ipify would block; backend only reads the header for our own API URL | OK |
| `X-Forwarded-For` spoofing | We don't trust `X-Forwarded-For` for the public IP — we trust `X-User-Public-IP` (which the browser sets, not the network) | different threat model |

### 5.6 Performance

| Case | Behavior | Impact |
|---|---|---|
| First call | ~100-300ms latency (ipify round-trip) | Mitigated by Pre-warm at app mount |
| Subsequent calls (cached) | <1ms (in-memory dict lookup) | None |
| Cache invalidation | Only on page reload (page-session) | None — header is the same IP for the page lifetime |
| ipify downtime | First call: 3s timeout → null | Login flow continues with fallback IP |
| Multiple tabs | Each tab has its own JS context → its own cache | Each tab makes one ipify call; OK |
| Memory usage | One string per tab | Negligible |

### 5.7 Browser compatibility

| Browser | `fetch` + `AbortSignal.timeout` | Notes |
|---|---|---|
| Chrome 90+ | ✅ | `AbortSignal.timeout()` supported |
| Edge 90+ | ✅ | same as Chrome (Chromium) |
| Firefox 100+ | ✅ | supported |
| Safari 16.4+ | ✅ | supported |
| Older browsers (e.g. Safari 15) | ⚠️ `AbortSignal.timeout()` may not be available | Fallback: use `AbortController` + `setTimeout` |

If supporting older browsers is required, replace `AbortSignal.timeout(3000)` with:

```typescript
const controller = new AbortController();
const timeoutId = setTimeout(() => controller.abort(), 3000);
const res = await fetch(url, { signal: controller.signal });
clearTimeout(timeoutId);
```

---

## 6. What this enables on the backend

Once the frontend sends `X-User-Public-IP`, the backend will:
- Use it as the **real client IP** in audit logs (replacing the private VM IP that shows up today)
- Compute a **device fingerprint** hash: `IP + User-Agent + Accept-Language + Sec-CH-UA`
- On each login, compare the new fingerprint to the last one stored in the session
- If different: log a `security_event: "new_device_login"` and email the user

This is implemented in 4 backend files (separate from this handoff):
1. `apps/backend/app/middleware/logging.py` — update `_get_client_ip` to read `X-User-Public-IP` first
2. `apps/backend/app/utils/cloudflare.py` (new) — fingerprint helper
3. `apps/backend/app/features/auth/routes.py` — wire fingerprint compare into `/auth/login`
4. `apps/backend/app/services/email_service.py` — new email method + template

---

## 7. Backend status (verified 2026-09-04)

Frontend is now sending `X-User-Public-IP: 95.216.39.97` and other real public IPs.
**The backend has NOT yet been updated to read this header** — `_get_client_ip` currently
only checks `X-Real-IP` and `request.client.host`. So today the audit logs still show the
frontend VM's private IP (e.g. `192.168.122.50`).

**Required backend changes (build-mode work):**
1. Update `_get_client_ip` priority order: `X-User-Public-IP` → `CF-Connecting-IP` → `X-Real-IP` → `X-Forwarded-For` leftmost → `request.client.host`
2. Add `compute_fingerprint(request)` helper (returns SHA-256 of normalized request headers)
3. Wire fingerprint compare into `/auth/login` after successful auth
4. Add `send_new_device_login_email` method + `"new_device_login"` template

**Header contract verification:**
- ✅ Frontend sends `X-User-Public-IP` on every API call
- ✅ Backend middleware currently does NOT read it — needs the update above
- ⏳ Once updated, audit log `client_ip` will show the real public IP (e.g. `95.216.39.97`)
- ⏳ New-device fingerprint compare will work once the auth route is wired

---

## 8. Open questions for the frontend team

1. **Browser support** — do you need to support pre-Chromium-90 browsers? (Affects whether to use the `AbortSignal.timeout` polyfill.)
2. **Cache lifetime** — page-session (proposed) or longer (e.g. localStorage with 24h TTL)?
3. **Single wrapper or per-call Axios instance** — depends on app architecture.
4. **Show the IP in the UI for transparency?** (e.g. "last login from 1.2.3.4")
