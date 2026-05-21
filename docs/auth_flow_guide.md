# Authentication Flow Guide (Curl)

This document outlines the authentication flow for the Backend API, which uses **In-Memory Session Authentication** backed by **Keycloak**.

## Core Concept
1. **Keycloak** is the source of truth for identities.
2. **Backend** handles the token exchange with Keycloak.
3. **Memory** stores the Keycloak tokens in a local dictionary, associated with a `session_id`.
4. **Client (You)** only sees the `session_id` cookie.

---

## 1. Signup
Create a new user. This registers the user in Keycloak and syncs them to Redmine.

```bash
curl -X POST "http://95.216.39.97:8086/server/auth/signup" \
     -H "Content-Type: application/json" \
     -d '{
       "username": "test_user",
       "email": "test@example.com",
       "password": "Password123!"
     }'
```

## 2. Login
Authenticate and start a session.

```bash
# -c cookies.txt: Saves the session_id cookie returned by the server
curl -X POST "http://95.216.39.97:8086/server/auth/login?username=test_user&password=Password123!" \
     -c cookies.txt \
     -i
```

> [!IMPORTANT]
> The backend expects `username` and `password` as **query parameters** in the login request.

## 3. Verify Session
Check who is currently logged in based on the session cookie.

```bash
curl -X GET "http://95.216.39.97:8086/server/api/me" \
     -b cookies.txt
```

## 4. Protected API Call
Access secured resources (e.g., recordings).

```bash
# -b cookies.txt: Sends the saved session cookie
curl -X GET "http://95.216.39.97:8086/server/api/v1/recordings" \
     -b cookies.txt
```

## 5. Refresh Session
If your session is nearing expiry, you can refresh it. The backend will use the Keycloak refresh token stored in memory to get a new session.

```bash
# -b: sends current session cookie
# -c: saves the NEW session cookie returned by the server
curl -X POST "http://95.216.39.97:8086/server/auth/refresh" \
     -b cookies.txt \
     -c cookies.txt \
     -i
```

## 6. Logout
End the session and revoke tokens.

```bash
curl -X POST "http://95.216.39.97:8086/server/auth/logout" \
     -b cookies.txt \
     -c cookies.txt \
     -i
```

---

## 7. Inspecting Sessions
> [!NOTE]
> The system has been migrated from Redis to **In-Memory** storage. Sessions are now stored in a local dictionary on the server and are lost when the server restarts.

### Find your Session ID
Look in your `cookies.txt` file for a string like `VzG...`.

---

## Sessions vs Tokens: How they coexist

This project uses a **Server-Side Session** pattern that abstracts away **JWT Tokens**.

| Entity | Location | Purpose |
| :--- | :--- | :--- |
| **Session ID** | Client (Cookie) | An opaque string (`session_...`) used to identify you to the backend. |
| **Refresh Token** | Server (Memory) | A long-lived Keycloak token used by the backend to get new Access Tokens without asking for your password. |
| **Access Token** | Server (In-Memory) | A short-lived Keycloak JWT used by the backend to verify your identity/roles for a single request. |

### The "Double-Layer" Auth Flow:
1.  **Request Phase**: You send a `session_id`.
2.  **Lookup Phase**: Backend looks up `session_id` in its local memory store and finds your stored **Keycloak Refresh Token**.
3.  **Validation Phase**: 
    - If the backend needs a fresh token, it uses the Refresh Token to talk to Keycloak.
    - Keycloak validates the user is still active.
4.  **Action Phase**: Backend proceeds with your request (e.g., fetching recordings).

### Why this approach?
- **Security**: JWTs (which contain sensitive user data) never touch the browser. They stay safe in the backend.
- **Revocation**: If you logout, we delete the session from memory immediately. With pure JWTs, you often have to wait for the token to expire.

---

## Comparison: Session vs. Pure JWT

If we were to switch to a **Pure JWT** approach (where the client handles tokens), here is how it would compare:

| Feature | Current (Session + Memory) | Pure JWT (Client-Side) |
| :--- | :--- | :--- |
| **Storage Location** | **Server (Memory)** | **Client (Browser)** |
| **What Client Sees** | Opaque Session ID | Encoded JWT (User info visible) |
| **Revocation** | **Instant** (Kill session in memory) | **Delayed** (Wait for expiry) |
| **Frontend Complexity** | Low (Browser handles cookies) | High (Manual token management) |

### Client-Side Storage Strategies (For Pure JWT)
If you moved tokens to the client, you would have to choose where to store them:

1.  **LocalStorage / SessionStorage:**
    *   *Pros:* Extremely easy to implement.
    *   *Cons:* **Vulnerable to XSS.** Malicious scripts can steal your token easily.
2.  **In-Memory (JavaScript Variable):**
    *   *Pros:* Most secure against theft (XSS).
    *   *Cons:* **Lost on page refresh.** Requires complex "Silent Refresh" logic to keep the user logged in.
3.  **HTTP-Only Cookie (Current Method):**
    *   *Pros:* Secure against XSS. Browser handles storage automatically.
    *   *Cons:* Vulnerable to CSRF (requires SameSite flags, which we use).

**Verdict:** Our current **Session-ID-to-Memory** approach provides the best balance of security (tokens stay server-side) and simplicity (frontend doesn't manage JWTs).
