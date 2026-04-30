# Frontend Integration Guide (Next.js)

This guide explains how to connect your Next.js frontend to the Backend Auth API.

## 1. Important: Cookies & Credentials
Since our architecture uses **HTTP-Only Cookies** for sessions, your frontend must send credentials with every request.

*   **Fetch API**: Use `{ credentials: 'include' }`
*   **Axios**: Use `{ withCredentials: true }`

---

## 2. TypeScript Types
Copy these interfaces into your Next.js types file (e.g., `types/auth.ts`):

```typescript
export interface UserProfile {
  sub: string;        // Unique ID from Keycloak
  username: string;
  email: string;
  roles: string[];    // Array of roles like "admin", "user", etc.
}

export interface LoginResponse {
  message: string;
  user: UserProfile;
}
```

---

## 3. API Endpoints

### Login
Sends credentials to the backend. The backend will set a `session_id` cookie automatically.

```typescript
const login = async (username, password): Promise<LoginResponse | null> => {
  const response = await fetch(`http://localhost/auth/login?username=${username}&password=${password}`, {
    method: 'POST',
    credentials: 'include',
  });
  
  if (response.ok) {
    const data: LoginResponse = await response.json();
    console.log("Logged in user:", data.user);
    return data;
  }
  return null;
};
```

### Get Current User Profile
Check if the user is logged in and get their data.

```typescript
const getProfile = async (): Promise<UserProfile | null> => {
  const response = await fetch('http://localhost/api/me', {
    method: 'GET',
    credentials: 'include',
  });
  
  if (response.status === 200) {
    const user: UserProfile = await response.json();
    return user;
  }
  return null; // Not logged in
};
```

### Logout
Clears the session on both Backend and Keycloak.

```typescript
const logout = async () => {
  await fetch('http://localhost/auth/logout', {
    method: 'POST',
    credentials: 'include',
  });
  window.location.href = '/login'; // Redirect user
};
```

---

## 3. Environment Variables (.env.local)

In your Next.js project, add these:

```env
NEXT_PUBLIC_API_URL=http://localhost
```

---

## 4. Common Issues & Fixes

### CORS Errors
If you see `CORS` errors, ensure your Next.js URL (e.g., `http://localhost:3000`) is listed in the backend `CORS_ORIGINS` in `apps/backend/app/config.py`.

### Cookies not being sent
If `http://localhost/api/me` returns `401 Unauthorized` even after login, double-check that you added `credentials: 'include'` to your fetch call.

### Secure Cookies
In development, we use `COOKIE_SECURE = False`. When you move to production (HTTPS), you must change this to `True` in the backend config.
