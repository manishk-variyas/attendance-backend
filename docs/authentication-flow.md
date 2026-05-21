# Authentication Flow Guide

A complete guide to understanding how authentication works in this application.

---

## Overview

This application uses a **BFF (Backend-For-Frontend)** pattern with **Keycloak** as the identity provider. The key concept is:
- **Keycloak** handles user identity, password storage, and token management
- **Backend** creates a session for the browser (browser never sees Keycloak tokens)

```
User Browser → Nginx → Backend → Keycloak (internal only)
                    ↓
              Session Store (in-memory)
```

---

## Login Flow

### Step 1: User submits credentials

Frontend sends username and password to backend:

```
POST /auth/login?username=user123&password=secretpass
```

> Note: Credentials are sent as query parameters, not JSON body

### Step 2: Backend validates with Keycloak

Backend forwards credentials to Keycloak using **ROPC (Resource Owner Password Credentials)** flow:

```
POST http://keycloak:8080/realms/attendance-app/protocol/openid-connect/token

Form data:
- grant_type: password
- client_id: backend-client
- client_secret: best-practice-secret-12345
- username: user123
- password: secretpass
```

### Step 3: Keycloak returns tokens

If valid, Keycloak returns:
| Token | Purpose | Lifetime |
|-------|---------|----------|
| `access_token` | Proves user identity | ~5 minutes |
| `refresh_token` | Get new tokens without re-login | ~30 days |
| `id_token` | User profile info | ~5 minutes |

### Step 4: Backend extracts user info

Backend decodes the `access_token` (JWT) to extract:
- `sub` - unique user ID from Keycloak
- `username` - login name
- `email` - user email
- `roles` - user permissions

### Step 5: Backend creates session

Backend stores session in **in-memory dictionary**:

```python
# session.py
_session_store: Dict[str, dict] = {}
```

Session data stored:
```json
{
  "sub": "2a0a2f4c-abc123",
  "username": "user123",
  "email": "user@example.com",
  "roles": ["default-roles-attendance-app", "offline_access"],
  "kc_refresh_token": "eyJhbGc..."
}
```

**Important**: The refresh token is stored server-side, NOT sent to the browser.

### Step 6: Backend returns cookie

Backend sends HTTP-only cookie to browser:

```
Set-Cookie: session_id=qChQ02pX9drHxDakrjnCC0ovX02B7byfEt3lD3zEZPY;
            HttpOnly;
            Secure;
            SameSite=lax;
            Max-Age=86400
```

### What the browser stores

The browser **only** sees a random session ID. It never sees:
- ❌ JWTs (access_token, refresh_token, id_token)
- ❌ Passwords
- ❌ Keycloak URLs

```
Browser Cookie Store:
┌─────────────────┬──────────────────────────┐
│ Name            │ Value                     │
├─────────────────┼──────────────────────────┤
│ session_id      │ qChQ02pX9drHxD...        │
└─────────────────┴──────────────────────────┘
```

---

## Session Management

### Storage

Sessions are stored in **in-memory Python dictionary** (`/apps/backend/app/features/auth/services/session.py`):

```python
_session_store: Dict[str, dict] = {}
```

**Note**: Redis was disabled. Sessions are lost on server restart.

### Session Data Structure

```python
{
    "sub": str,           # Keycloak user ID
    "username": str,     # Login name
    "email": str,        # User email
    "roles": List[str],  # User roles from Keycloak
    "kc_refresh_token": str  # Keycloak refresh token
}
```

### Session Lifecycle

| Action | What Happens |
|--------|--------------|
| **Login** | Creates new session with random session_id |
| **Protected Request** | Validates session_id, returns user data |
| **Session Expiry** | After 24 hours (SESSION_EXPIRE_HOURS) |
| **Logout** | Deletes session, revokes Keycloak token |

### Checking Session

Every protected endpoint uses `get_current_user` dependency:

```python
async def get_current_user(request: Request) -> dict:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    session_data = await get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    return session_data
```

---

## Token Storage

### Where Each Token Lives

| Token Type | Location | Accessed By |
|------------|----------|-------------|
| `session_id` | Browser cookie (HttpOnly) | Browser automatically |
| `kc_refresh_token` | Backend memory (session store) | Backend only |
| `access_token` | Backend memory (temporary, not stored) | Backend only |

### Why This Design?

1. **session_id** in cookie:
   - Opaque identifier (can't guess)
   - HttpOnly prevents XSS theft
   - SameSite=lax prevents CSRF

2. **refresh_token** in server memory:
   - Browser never has it = can't be stolen by XSS
   - Can be revoked instantly on logout
   - Used to get new access tokens automatically

3. **access_token** not stored:
   - Obtained on-demand from Keycloak when needed for API calls
   - Never persists anywhere

---

## Signup Flow

### Step 1: User submits signup data

```
POST /auth/signup
{
  "username": "newuser",
  "password": "securepassword123"
}
```

### Step 2: Backend validates

- Username: alphanumeric + underscore, 3-30 chars
- Password: minimum 8 characters

### Step 3: Backend creates user in Keycloak

Backend uses Keycloak Admin API:

```
POST http://keycloak:8080/realms/attendance-app/admin/users

{
  "username": "newuser",
  "enabled": true,
  "credentials": [{
    "type": "password",
    "value": "securepassword123",
    "temporary": false
  }]
}
```

### Step 4: Backend syncs to Redmine

A background task creates the user in Redmine (async):

```python
# Background task (non-blocking)
create_background_task(sync_user_to_redmine(username, email))
```

This keeps the signup response fast.

### Step 5: Auto-login

After successful signup, the backend performs login automatically and returns the session cookie - user is immediately logged in.

---

## Session-Based vs Keycloak-Based Auth

This is a common point of confusion. Here's the difference:

### Pure Keycloak-Based (JWT stored in browser)

```
┌──────────┐     ┌─────────────┐     ┌──────────┐
│ Browser  │────>│   Backend   │────>│ Keycloak │
│          │     │  (stateless)     │          │
└──────────┘     └─────────────┘     └──────────┘
       │
       └─> Stores JWT in localStorage
       └─> Sends JWT in Authorization header
```

**Pros:**
- Stateless (no server storage)
- Scales easily

**Cons:**
- JWT stored in browser (vulnerable to XSS)
- Hard to revoke (must use token blocklist)
- Complex logout (must invalidate at identity provider)

---

### This Project's Approach (BFF Pattern)

```
┌──────────┐     ┌─────────────┐     ┌──────────┐
│ Browser  │────>│   Backend   │────>│ Keycloak │
│          │     │ (with session)    │          │
└──────────┘     └─────────────┘     └──────────┘
       │
       └─> Stores session_id in cookie (HttpOnly)
       └─> Sends cookie with every request
              │
              └─> Backend looks up session in memory
              └─> Backend uses stored refresh_token to get JWTs
```

**Pros:**
- JWTs never leave the backend (secure)
- Instant logout (just delete session)
- Session rotation prevents fixation attacks
- Backchannel logout support

**Cons:**
- Sessions use server memory
- Not horizontally scalable without Redis

### Summary

| Aspect | Pure JWT | This Project (BFF) |
|--------|----------|---------------------|
| Token storage | Browser (localStorage) | Server memory |
| XSS vulnerability | High | Low (HttpOnly cookie) |
| Logout complexity | Complex | Simple (delete session) |
| Scalability | Stateless | Requires session store |
| Token revocation | Blocklist needed | Instant |

---

## Logout Flow

### Step 1: Frontend calls logout

```
POST /auth/logout
Cookie: session_id=qChQ02pX9drHxDakrjnCC0ovX02B7byfEt3lD3zEZPY
```

### Step 2: Backend does three things

**2a. Revoke Keycloak token**

```python
# Call Keycloak revocation endpoint
POST http://keycloak:8080/realms/attendance-app/protocol/openid-connect/logout
token: <kc_refresh_token>
```

**2b. Delete session from memory**

```python
await delete_session(session_id)
# Removes from _session_store dict
```

**2c. Clear cookie**

```python
response.delete_cookie("session_id", path="/")
```

### Result

- ❌ Cookie removed from browser
- ❌ Session deleted from backend memory
- ❌ Refresh token invalidated at Keycloak
- Next request returns `401 Unauthorized`

---

## Backchannel Logout (Keycloak-Initiated)

This handles the case when an admin disables a user in Keycloak admin console.

### Flow

1. Admin disables user in Keycloak Console
2. Keycloak sends logout_token to backend:

```
POST /auth/backchannel-logout
logout_token=<signed-jwt>
```

3. Backend verifies token, extracts user `sub`
4. Backend deletes ALL sessions for that user
5. User's next request gets `401`

This ensures users are logged out everywhere when disabled in Keycloak.

---

## Session Refresh

Access tokens expire after ~5 minutes. The backend automatically refreshes using the stored refresh token.

### Flow

1. Frontend calls `/auth/refresh` with session cookie
2. Backend looks up session, gets `kc_refresh_token`
3. Backend calls Keycloak to exchange for new tokens
4. **Security**: Backend creates NEW session with NEW session_id (session rotation)
5. Returns new cookie

This prevents session fixation attacks - even if someone stole the old session_id, it becomes useless after refresh.

---

## Security Features

| Feature | Protection Against |
|---------|---------------------|
| **HttpOnly cookie** | XSS (JavaScript can't read session) |
| **SameSite=lax** | CSRF (cookie not sent on cross-site requests) |
| **Server-side tokens** | JWTs never touch browser |
| **Session rotation** | Session fixation attacks |
| **Token revocation** | Reused tokens after logout |
| **Backchannel logout** | Keycloak can invalidate sessions |
| **Rate limiting** | Brute force on login |

---

## Technical Considerations for the BFF Pattern

The Backend-For-Frontend (BFF) pattern was chosen for this project over traditional browser-based OIDC/OAuth2 flows based on a few key architectural considerations:

1. **Reduced Attack Surface**: Keeping OAuth tokens (Access, Refresh, ID) on the server generally helps mitigate the risk of Token Exfiltration via XSS attacks, as the browser only handles an opaque session cookie.
2. **Simplified Frontend Architecture**: The frontend application can avoid relying on complex OIDC client libraries (like `keycloak-js`), instead managing authentication state through a standard HTTP session cookie.
3. **Easier Revocation**: With server-side session management, a logout or backchannel logout can more reliably invalidate the session, reducing the reliance on short-lived access tokens expiring on the client side.
4. **Leveraging Cookie Security**: Utilizing HTTP-only, Secure, and SameSite cookie attributes allows us to lean on browser-enforced security mechanisms.

---

## Trade-offs of In-Memory Session Storage

Currently, the application uses an in-memory dictionary (`_session_store`) for session management. This approach comes with several trade-offs:

### Potential Advantages
- **Low Latency**: Session lookups happen in memory, which avoids the network overhead typically associated with an external database or cache.
- **Operational Simplicity**: Not requiring external dependencies (like Redis or Memcached) can make local development and initial deployments more straightforward.

### Known Limitations
- **Stateful Backend**: This approach introduces state to the backend. A user's session effectively becomes bound to the specific backend instance that handled their login.
- **Scaling Considerations**: If the application needs to scale across multiple backend instances, strategies like sticky sessions (session affinity) at the load balancer level might be necessary to maintain authentication state.
- **Ephemeral Data**: Active sessions are lost if the backend process restarts or crashes, which would require users to log in again.

*Note: As the project scales or if higher availability is required, the team might want to evaluate migrating `_session_store` to a distributed cache like Redis.*

---

## File References

| File | Purpose |
|------|---------|
| `app/features/auth/routes.py` | Login, logout, signup endpoints |
| `app/features/auth/dependencies.py` | `get_current_user` dependency |
| `app/features/auth/services/session.py` | In-memory session storage |
| `app/features/auth/services/keycloak.py` | Keycloak API calls |
| `app/core/config.py` | Auth configuration |