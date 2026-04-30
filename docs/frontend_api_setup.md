# Frontend API Setup Guide

How to integrate your frontend with the backend authentication system.

---

## Quick Start

```typescript
const API_BASE = "http://your-server"; // No trailing slash

// Login
const res = await fetch(`${API_BASE}/auth/login?username=${u}&password=${p}`, {
  method: "POST",
  credentials: "include", // Sends and receives cookies
});

// All subsequent requests automatically include the session cookie
// because of credentials: "include"
```

**The key rule:** Every request must include `credentials: "include"` so the browser sends the session cookie.

---

## Endpoints Reference

### 1. Login

Authenticates a user with username and password.

**Request**

```
POST /auth/login?username={username}&password={password}
```

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `username` | string | Query | Yes | Keycloak username |
| `password` | string | Query | Yes | User's password |

**Response — 200 OK**

```json
{
  "message": "Login successful",
  "user": {
    "sub": "2a0a2f4c-a07e-4465-9e51-1ef2bc3cc2c6",
    "username": "testuser",
    "email": null,
    "roles": ["default-roles-attendance-app", "offline_access", "uma_authorization"]
  }
}
```

**Response — 401 Unauthorized**

```json
{
  "detail": "Invalid credentials"
}
```

**Response — 429 Too Many Requests** (rate limited)

```json
{
  "detail": "Too many requests. Please try again later."
}
```

**Side effect:** Sets `session_id` cookie (HttpOnly, 24h TTL).

---

### 2. Get Current User

Returns the authenticated user's profile.

**Request**

```
GET /api/me
Cookie: session_id=abc123...
```

**Response — 200 OK**

```json
{
  "sub": "2a0a2f4c-a07e-4465-9e51-1ef2bc3cc2c6",
  "username": "testuser",
  "email": null,
  "roles": ["default-roles-attendance-app", "offline_access", "uma_authorization"]
}
```

**Response — 401 Unauthorized**

```json
{
  "detail": "Not authenticated"
}
```

or

```json
{
  "detail": "Invalid or expired session"
}
```

---

### 3. Refresh Session

Refreshes the user's session and rotates the session ID. Call this every ~4 minutes to keep the user logged in.

**Request**

```
POST /auth/refresh
Cookie: session_id=abc123...
```

**Response — 200 OK**

```json
{
  "message": "Session refreshed",
  "user": {
    "sub": "2a0a2f4c-a07e-4465-9e51-1ef2bc3cc2c6",
    "username": "testuser",
    "email": null,
    "roles": ["default-roles-attendance-app", "offline_access", "uma_authorization"]
  }
}
```

**Response — 401 Unauthorized**

```json
{
  "detail": "Session expired, please login again"
}
```

**Side effect:** Sets a **new** `session_id` cookie. The old one is invalidated.

---

### 4. Logout

Logs the user out from the app and revokes their Keycloak token.

**Request**

```
POST /auth/logout
Cookie: session_id=abc123...
```

or

```
GET /auth/logout
Cookie: session_id=abc123...
```

**Response — 200 OK**

```json
{
  "message": "Logout successful"
}
```

**Side effect:** Deletes the `session_id` cookie and revokes the Keycloak refresh token.

---

### 5. Health Check

Checks if the backend is running.

**Request**

```
GET /health
```

**Response — 200 OK**

```json
{
  "status": "ok"
}
```

---

## How to Use in Different Frontends

### Vanilla JavaScript / Fetch

```javascript
const API = "http://your-server";

async function login(username, password) {
  const res = await fetch(`${API}/auth/login?username=${encodeURIComponent(username)}&password=${encodeURIComponent(password)}`, {
    method: "POST",
    credentials: "include",
  });

  if (!res.ok) {
    const body = await res.json();
    throw new Error(body.detail || "Login failed");
  }

  return res.json();
}

async function getMe() {
  const res = await fetch(`${API}/api/me`, {
    credentials: "include",
  });

  if (!res.ok) throw new Error("Not authenticated");
  return res.json();
}

async function logout() {
  await fetch(`${API}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  // Redirect to login page after logout
  window.location.href = "/login";
}
```

### Axios

```javascript
import axios from "axios";

const api = axios.create({
  baseURL: "http://your-server",
  withCredentials: true, // Same as credentials: "include"
});

// Login
const { data } = await api.post("/auth/login", null, {
  params: { username: "testuser", password: "password" },
});

// Get user
const { data: user } = await api.get("/api/me");

// Refresh
const { data } = await api.post("/auth/refresh");

// Logout
await api.post("/auth/logout");
```

### React + useEffect (session refresh)

```tsx
import { useEffect, useState } from "react";

const API = "http://your-server";

function useSession() {
  const [user, setUser] = useState(null);

  // Refresh session every 4 minutes
  useEffect(() => {
    const refresh = async () => {
      try {
        const res = await fetch(`${API}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          setUser(data.user);
        } else {
          // Session expired, redirect to login
          window.location.href = "/login";
        }
      } catch {
        window.location.href = "/login";
      }
    };

    const interval = setInterval(refresh, 4 * 60 * 1000); // 4 minutes
    return () => clearInterval(interval);
  }, []);

  return { user };
}
```

### Next.js (App Router)

```tsx
// app/layout.tsx or a provider component
"use client";

import { useEffect } from "react";

export function SessionProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const refresh = async () => {
      try {
        await fetch("/auth/refresh", {
          method: "POST",
          credentials: "include",
        });
      } catch {
        // Session expired
      }
    };

    const interval = setInterval(refresh, 4 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  return children;
}
```

For Next.js, set up a rewrite so API calls go through the same domain:

```js
// next.config.js
module.exports = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://your-server/:path*",
      },
      {
        source: "/auth/:path*",
        destination: "http://your-server/auth/:path*",
      },
    ];
  },
};
```

---

## Cookie Handling

### What the browser does automatically

The backend sets an `HttpOnly` cookie. This means:

- **Browser automatically sends it** on every request to the same domain
- **JavaScript CANNOT read it** (no `document.cookie`)
- **You don't need to manually attach it** — just use `credentials: "include"`

### CORS Configuration

If your frontend is on a different domain (e.g., `https://app.example.com` → `https://api.example.com`), you need:

1. **Backend** must have your frontend URL in `CORS_ORIGINS` in `.env`
2. **Frontend** must set `credentials: "include"` (fetch) or `withCredentials: true` (axios)
3. **Frontend** must NOT use `*` in allowed origins — must be exact URL

### Same-site deployment (recommended)

If both frontend and backend are served from the same domain (e.g., Nginx serves frontend on `/` and proxies `/api` and `/auth` to backend), cookies work automatically with no CORS needed.

---

## Error Handling

### Standard error response format

All errors return:

```json
{
  "detail": "Human-readable error message"
}
```

### Common error codes

| Status Code | Meaning | What to do |
|-------------|---------|------------|
| `401` | Not authenticated or session expired | Redirect to login page |
| `429` | Rate limited (too many login attempts) | Show "try again later" message |
| `500` | Server error | Show generic error, retry later |
| `502` | Backend unreachable (Nginx error) | Show "service unavailable" |

### Recommended error handling pattern

```javascript
async function apiCall(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    credentials: "include",
  });

  if (res.status === 401) {
    // Session expired, redirect to login
    window.location.href = "/login";
    throw new Error("Session expired");
  }

  if (res.status === 429) {
    throw new Error("Too many attempts. Please wait.");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
```

---

## Correlation ID (Optional)

The backend supports an `X-Correlation-ID` header for request tracing. If you want to track a user's request across all log files:

```javascript
const correlationId = `req-${crypto.randomUUID().slice(0, 12)}`;

fetch("/api/me", {
  headers: {
    "X-Correlation-ID": correlationId,
  },
  credentials: "include",
});
```

The backend returns this same ID in the `X-Correlation-ID` response header.

---

## Complete Frontend Flow

```
1. User visits app
   → Check session: GET /api/me (credentials: "include")
   → 401? Show login page

2. User logs in
   → POST /auth/login?username=X&password=Y (credentials: "include")
   → 200? Store user info, redirect to dashboard
   → 401? Show "wrong credentials" error
   → 429? Show "too many attempts" error

3. User navigates app
   → All API calls use credentials: "include"
   → Cookie sent automatically by browser

4. Every 4 minutes
   → POST /auth/refresh (credentials: "include")
   → 401? Redirect to login

5. User clicks logout
   → POST /auth/logout (credentials: "include")
   → Redirect to login page
```
