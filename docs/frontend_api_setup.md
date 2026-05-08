# Frontend API Setup Guide

How to integrate your frontend with the backend authentication system when backend is on a VM.

---

## Quick Start

### For VM Deployment (Backend on VM, Frontend Outside)

Since your backend runs on a VM (behind nginx on port 80), use the **VM's IP address**:

```typescript
// Replace 192.168.122.101 with your VM's actual IP address
const API_BASE = "http://192.168.122.101"; // No trailing slash, port 80 is default

// Login
const res = await fetch(`${API_BASE}/auth/login?username=${u}&password=${p}`, {
  method: "POST",
  credentials: "include", // Sends and receives cookies
});

// All subsequent requests automatically include the session cookie
// because of credentials: "include"
```

**Important:** Every request must include `credentials: "include"` so the browser sends the session cookie.

**Important:** If you've set up a domain/DNS for your VM, use that instead:
```typescript
const API_BASE = "https://api.yourdomain.com"; // or http://api.yourdomain.com
```

---

## Full VM URLs (Ready to Use)

**Base URL:** `http://192.168.122.101` (nginx on port 80)

| Endpoint | Full URL | Method |
|----------|----------|--------|
| Health Check | `http://192.168.122.101/health` | GET |
| Signup | `http://192.168.122.101/auth/signup` | POST |
| Login | `http://192.168.122.101/auth/login?username=X&password=Y` | POST |
| Refresh Session | `http://192.168.122.101/auth/refresh` | POST |
| Logout | `http://192.168.122.101/auth/logout` | POST |
| Get Current User | `http://192.168.122.101/api/me` | GET |

### Test Commands

```bash
# Health check
curl http://192.168.122.101/health
# Returns: {"status":"ok"}

# Login
curl -X POST "http://192.168.122.101/auth/login?username=your_user&password=your_pass" -c cookies.txt

# Get current user (with session cookie)
curl http://192.168.122.101/api/me -b cookies.txt
```

---

## Endpoints Reference

All endpoints are relative to the VM IP. Base URL: `http://192.168.122.101`

### 1. Login

Authenticates a user with username and password.

**Request**

```
POST http://192.168.122.101/auth/login?username={username}&password={password}
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

### 2. Signup (Create New User)

Creates a new user account in Keycloak.

**Request**

```
POST http://192.168.122.101/auth/signup
Content-Type: application/json

{
  "username": "newuser",
  "email": "newuser@example.com",
  "password": "Password123"
}
```

| Field | Type | Required | Validation |
|-------|------|----------|-----------|
| `username` | string | Yes | 3-30 chars, letters/numbers/underscore only |
| `email` | string | Yes | Valid email format |
| `password` | string | Yes | Minimum 8 characters |

**Request Payload (JavaScript)**

```javascript
const signupData = {
  username: "newuser",
  email: "newuser@example.com",
  password: "Password123"
};

const res = await fetch("http://192.168.122.101/auth/signup", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",  // IMPORTANT: Required!
  },
  body: JSON.stringify(signupData),
  credentials: "include",
});
```

**Response — 201 Created**

```json
{
  "message": "User created successfully",
  "user_id": "012ed70c-da2d-4eb9-92cb-0e86a677fc32"
}
```

**Response — 409 Conflict** (username or email already exists)

```json
{
  "detail": "Username already exists"
}
```

or

```json
{
  "detail": "Email already exists"
}
```

**Response — 422 Unprocessable Content** (validation error)

```json
{
  "detail": "Validation error message"
}
```

Common causes:
- Missing `Content-Type: application/json` header
- Invalid JSON format
- Email not valid format
- Username has special characters (only letters, numbers, underscore allowed)
- Password too short

**Rate limit:** 5 requests per minute per IP.

---

### 3. Get Current User

Returns the authenticated user's profile.

**Request**

```
GET http://192.168.122.101/api/me
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
POST http://192.168.122.101/auth/refresh
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
POST http://192.168.122.101/auth/logout
Cookie: session_id=abc123...
```

or

```
GET http://192.168.122.101/auth/logout
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
GET http://192.168.122.101/health
```

**Response — 200 OK**

```json
{
  "status": "ok"
}
```

---

## Audio & Recording APIs

---

## Redmine Ticket Discovery

Before uploading a recording, the frontend should fetch the list of active tickets (issues) assigned to the logged-in user to allow them to select the correct `ticketId`.

### 5. Get My Issues

Returns a list of Redmine issues assigned to the authenticated user.

**Request**

```
GET http://192.168.122.101/api/my-issues
```

**Response — 200 OK**

```json
[
  {
    "id": 123,
    "subject": "Implement Login Flow",
    "project_name": "Attendance App",
    "status": "In Progress",
    "priority": "High"
  },
  {
    "id": 124,
    "subject": "Bug: Audio not saving",
    "project_name": "Attendance App",
    "status": "New",
    "priority": "Immediate"
  }
]
```

---

### 6. Upload Recording


Uploads a new audio recording with metadata.

**Request**

```
POST http://192.168.122.101/api/upload
Content-Type: multipart/form-data
```

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `audio` | File | Form | Yes | Audio file. **Allowed types:** `.mp3`, `.wav`, `.ogg`, `.webm`, `.m4a`. |
| `email` | string | Form | Yes | User's email |
| `ticketId` | string | Form | Yes | Related Redmine ticket ID. Used to fetch project, priority, and status. |



**Response — 200 OK**


```json
{
  "id": "69fd77aedaaa03774af22719",
  "email": "test@gmail.com",
  "ticket_id": "123",
  "project": "DevOps",
  "priority": "HIGH",
  "status": "open",
  "filename": "audio.mp3",
  "recording_url": "http://192.168.122.101/api/recordings/...",
  "is_played": false,
  "created_at": "2026-05-08T05:42:06.280Z"
}
```

### 7. Get My Recordings

Retrieves all recordings for a specific user.

**Request**

```
GET http://192.168.122.101/api/my-recordings/{email}
```

**Response — 200 OK**

```json
[
  {
    "id": "69fd77aedaaa03774af22719",
    "email": "test@gmail.com",
    "filename": "audio.mp3",
    "recording_url": "...",
    "is_played": false,
    "created_at": "2026-05-08T05:42:06.280Z"
  }
]
```

### 8. Mark Recording as Played

Marks a specific recording as played.

**Request**

```
POST http://192.168.122.101/api/mark-played
Content-Type: application/json
```

```json
{
  "email": "test@gmail.com",
  "recordingUrl": "http://192.168.122.101/api/recordings/..."
}
```

### 9. Delete Recording

Deletes a recording from storage and database.

**Request**

```
DELETE http://192.168.122.101/api/delete-recording
Content-Type: application/json
```

```json
{
  "email": "test@gmail.com",
  "recordingUrl": "http://192.168.122.101/api/recordings/..."
}
```

---

## How to Use in Different Frontends


### Vanilla JavaScript / Fetch

```javascript
// Replace with your VM's IP address
const API = "http://192.168.122.101";

async function signup(username, email, password) {
  const res = await fetch(`${API}/auth/signup`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",  // IMPORTANT!
    },
    body: JSON.stringify({ username, email, password }),
    credentials: "include",
  });

  if (!res.ok) {
    const body = await res.json();
    throw new Error(body.detail || "Signup failed");
  }

  return res.json();
}

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

// Replace with your VM's IP address
const api = axios.create({
  baseURL: "http://192.168.122.101",
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

// Replace with your VM's IP address
const API = "http://192.168.122.101";

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

// Replace with your VM's IP address
const API = "http://192.168.122.101";

export function SessionProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const refresh = async () => {
      try {
        await fetch(`${API}/auth/refresh`, {
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

For Next.js, you can alternatively set up rewrites in next.config.js so API calls go through the same domain (recommended for production):

```js
// next.config.js
module.exports = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://192.168.122.101/:path*",
      },
      {
        source: "/auth/:path*",
        destination: "http://192.168.122.101/auth/:path*",
      },
    ];
  },
};
```

This way, frontend calls to `/auth/login` are automatically proxied to your VM backend.

---

## Cookie Handling

### What the browser does automatically

The backend sets an `HttpOnly` cookie. This means:

- **Browser automatically sends it** on every request to the same domain
- **JavaScript CANNOT read it** (no `document.cookie`)
- **You don't need to manually attach it** — just use `credentials: "include"`

### CORS Configuration

Since your frontend is on a **different domain** (your frontend machine) than the backend (VM), you need to configure CORS on the backend.

1. **Backend** must have your frontend URL in `CORS_ORIGINS` in `apps/backend/.env`
2. **Frontend** must set `credentials: "include"` (fetch) or `withCredentials: true` (axios)
3. **Frontend** must NOT use `*` in allowed origins — must be exact URL

Update your backend `.env`:

```bash
# In apps/backend/.env - add your frontend URL
CORS_ORIGINS=["http://localhost:5173","http://localhost:3000","http://your-frontend-machine-ip:5173"]
```

Then restart the backend container.

**For production**, it's recommended to use nginx as a reverse proxy on the VM and serve both frontend and backend from the same domain, avoiding CORS issues entirely.

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

fetch("http://192.168.122.101/api/me", {
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
API_BASE = http://192.168.122.101

1. User visits app
   → Check session: GET http://192.168.122.101/api/me (credentials: "include")
   → 401? Show login page

2. User logs in
   → POST http://192.168.122.101/auth/login?username=X&password=Y (credentials: "include")
   → 200? Store user info, redirect to dashboard
   → 401? Show "wrong credentials" error
   → 429? Show "too many attempts" error

3. User navigates app
   → All API calls use credentials: "include"
   → Cookie sent automatically by browser

4. Every 4 minutes
   → POST http://192.168.122.101/auth/refresh (credentials: "include")
   → 401? Redirect to login

5. User clicks logout
   → POST http://192.168.122.101/auth/logout (credentials: "include")
   → Redirect to login page
```
