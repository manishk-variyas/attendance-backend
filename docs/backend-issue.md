# Backend Security Issues — attendance.variyaslabs.com

Backend: **FastAPI (Pydantic)** behind **nginx**. Origin IP `95.216.39.97` responds directly.

---

## 🔴 Critical / High

### B1. Rate‑limit bypass via `CF-Connecting-IP` header spoofing (brute force)
- The login rate limiter keys on the **`CF-Connecting-IP` header**, which the backend trusts blindly. The header is trivially forgeable because the origin is reachable directly, not only via Cloudflare.
- **Verified:** with `CF-Connecting-IP: 9.9.9.1`, 4+ consecutive `401 Invalid credentials` with no per‑IP throttle. Without the header, `429 Too many login attempts from this network` kicks in after ~3 tries.
- **Impact:** unlimited password guessing / credential stuffing. Any IP‑based controls are spoofable.

### B2. Cloudflare Turnstile NOT enforced server‑side
- The web UI sends a `turnstileToken`, but `/api/auth/login` proceeds to credential checking with an **empty/missing token** → `401 Invalid credentials` instead of a Turnstile validation error.
- **Impact:** the CAPTCHA/bot protection is cosmetically bypassable at the API level.

### B3. Password‑reset email bombing via the same spoof
- `POST /api/auth/forgot-password` can be fired repeatedly (returned `200` for 5 different spoofed IPs before a global limiter caught it).
- **Impact:** unbounded password‑reset emails to any address → spam / email‑bombing.

### B4. Origin directly exposed → WAF bypass
- The backend at `95.216.39.97` serves `api/*` directly and honors client‑supplied `CF-Connecting-IP`.
- **Impact:** an attacker targeting the origin IP skips Cloudflare entirely (WAF, Turnstile, challenge pages).

---

## 🟠 Medium

### B5. Pydantic 422 error disclosure
- `POST /api/auth/login` with `{}` returns a 422 exposing internal field names/types:
  ```json
  {"detail":[{"type":"missing","loc":["body","username"],"msg":"Field required",...}]}
  ```
- Also `Server: nginx` + `x-process-time` headers leak stack/timing details.

### B6. Rate limiting only per‑IP / per‑network, no per‑account
- Lockout is keyed on (spoofable) IP/network, with no per‑account backoff. A distributed attack is unthrottled.

### B7. Reset‑password token strength — UNVERIFIED
- `POST /api/auth/reset-password` exists (validates `token` via Pydantic). Token entropy/lifetime/single‑use could not be tested without a real token. Verify it is server‑generated, long, and single‑use.

---

## 🟡 Info / Improvements

- Missing `robots.txt` (serves the 404 app page instead of a real robots file).
- Generic help: confirm no user enumeration is introduced by timing differences on login/forgot-password.

---

## ✅ Found OK
- **No user enumeration** on login (`Invalid credentials`) or forgot‑password (generic *"If the email is registered…"*).
- **No reflected CORS** with foreign `Origin` (no `Access-Control-Allow-Origin` emitted).
- **Protected endpoints return 401/404** when unauthenticated (e.g. `/server/api/redmine/attachments/*` → 401, no data leak).
- Rate limiting exists — it's just bypassable.

---

## Backend‑layer remediation
1. Stop trusting `CF-Connecting-IP` / `X-Forwarded-For` for security decisions; derive IP from the peer connection, or verify the connection comes from Cloudflare ranges.
2. Firewall the origin so only Cloudflare (or your ops IPs) can reach it.
3. Validate Turnstile **server‑side** on login and forgot‑password before any processing.
4. Add **per‑account** lockout + exponential backoff (keep per‑IP as secondary, not primary).
5. Customize Pydantic 422 serialization; remove `x-process-time`; tighten server banner.
6. Verify reset‑token generation (length, entropy, single‑use, expiry).