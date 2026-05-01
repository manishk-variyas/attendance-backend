# Next.js Pages Router - Auth Integration Guide

This guide explains how to integrate Next.js (Pages Router) with the backend authentication system.

## Table of Contents
- [Setup](#setup)
- [Login](#login)
- [Logout](#logout)
- [Protected Routes](#protected-routes)
- [Get Current User](#get-current-user)

---

## Setup

### 1. Create API Client Utility

Create `lib/auth.ts`:

```typescript
// lib/auth.ts

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost'

interface LoginResponse {
  message: string
  user: {
    sub: string
    username: string
    email: string | null
    roles: string[]
  }
}

interface User {
  sub: string
  username: string
  email: string | null
  roles: string[]
}

/**
 * Login user with credentials
 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch(`${API_URL}/auth/login?username=${encodeURIComponent(username)}&password=${encodeURIComponent(password)}`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || 'Login failed')
  }

  return response.json()
}

/**
 * Logout current user
 */
export async function logout(): Promise<void> {
  await fetch(`${API_URL}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}

/**
 * Get current logged in user
 */
export async function getCurrentUser(): Promise<User | null> {
  try {
    const response = await fetch(`${API_URL}/api/me`, {
      method: 'GET',
      credentials: 'include',
    })

    if (!response.ok) {
      return null
    }

    return response.json()
  } catch {
    return null
  }
}

/**
 * Check if user is authenticated
 */
export async function isAuthenticated(): Promise<boolean> {
  const user = await getCurrentUser()
  return user !== null
}
```

### 2. Environment Variables

Create `.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:80
```

---

## Login

### Login Page - `pages/login.tsx`

```tsx
import { useState } from 'react'
import { useRouter } from 'next/router'
import Link from 'next/link'
import { login } from '../lib/auth'

export default function LoginPage() {
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(username, password)
      router.push('/dashboard')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h1>Login</h1>
      
      <form onSubmit={handleSubmit}>
        <div>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        
        <div>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        
        {error && <div>{error}</div>}
        
        <button type="submit" disabled={loading}>
          {loading ? 'Logging in...' : 'Login'}
        </button>
      </form>
      
      <p>
        Don&apos;t have an account? <Link href="/register">Register</Link>
      </p>
    </div>
  )
}
```

---

## Logout

### Logout Button Component - `components/LogoutButton.tsx`

```tsx
import { useRouter } from 'next/router'
import { logout } from '../lib/auth'

export default function LogoutButton() {
  const router = useRouter()

  const handleLogout = async () => {
    try {
      await logout()
      router.push('/login')
    } catch (err) {
      console.error('Logout failed:', err)
    }
  }

  return (
    <button onClick={handleLogout}>
      Logout
    </button>
  )
}
```

### Using in Layout - `pages/_app.tsx`

```tsx
import type { AppProps } from 'next/app'
import { LogoutButton } from '../components/LogoutButton'

export default function MyApp({ Component, pageProps }: AppProps) {
  return (
    <div>
      <nav>
        <LogoutButton />
      </nav>
      <Component {...pageProps} />
    </div>
  )
}
```

---

## Protected Routes

### Higher-Order Component - `lib/protect.ts`

```tsx
import { GetServerSideProps, GetServerSidePropsContext, GetServerSidePropsResult } from 'next'
import { getCurrentUser } from './auth'

/**
 * Wrap getServerSideProps to protect a page
 * Redirects to login if user is not authenticated
 */
export function withAuth(
  getServerSideProps: GetServerSideProps
): GetServerSideProps {
  return async (context: GetServerSidePropsContext): Promise<GetServerSidePropsResult> => {
    const user = await getCurrentUser(context)

    if (!user) {
      return {
        redirect: {
          destination: '/login',
          permanent: false,
        },
      }
    }

    return getServerSideProps(context)
  }
}
```

### Protected Page Example - `pages/dashboard.tsx`

```tsx
import { GetServerSideProps } from 'next'
import { withAuth } from '../lib/protect'
import { getCurrentUser } from '../lib/auth'

interface DashboardProps {
  user: {
    username: string
    email: string | null
    roles: string[]
  }
}

export default function DashboardPage({ user }: DashboardProps) {
  return (
    <div>
      <h1>Dashboard</h1>
      <p>Welcome, {user.username}!</p>
      <p>Email: {user.email || 'Not provided'}</p>
      <p>Roles: {user.roles.join(', ')}</p>
    </div>
  )
}

export const getServerSideProps: GetServerSideProps = withAuth(async (context) => {
  const user = await getCurrentUser(context)

  return {
    props: {
      user,
    },
  }
})
```

### Client-Side Protected Route Check

```tsx
import { useEffect } from 'react'
import { useRouter } from 'next/router'
import { isAuthenticated } from '../lib/auth'

export default function ProtectedPage() {
  const router = useRouter()

  useEffect(() => {
    isAuthenticated().then((authenticated) => {
      if (!authenticated) {
        router.push('/login')
      }
    })
  }, [])

  return <div>Protected content</div>
}
```

---

## Get Current User

### Server-Side (in getServerSideProps)

```tsx
import { getCurrentUser } from '../lib/auth'

export const getServerSideProps: GetServerSideProps = async (context) => {
  const user = await getCurrentUser(context)

  if (!user) {
    return {
      redirect: {
        destination: '/login',
        permanent: false,
      },
    }
  }

  return {
    props: {
      user,
    },
  }
}
```

### Client-Side

```tsx
import { useEffect, useState } from 'react'
import { getCurrentUser } from '../lib/auth'

export default function ProfilePage() {
  const [user, setUser] = useState<{ username: string; email: string | null } | null>(null)

  useEffect(() => {
    getCurrentUser().then(setUser)
  }, [])

  if (!user) {
    return <div>Loading...</div>
  }

  return (
    <div>
      <h1>Profile</h1>
      <p>Username: {user.username}</p>
      <p>Email: {user.email}</p>
    </div>
  )
}
```

---

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login?username=&password=` | Login user |
| POST | `/auth/logout` | Logout user |
| GET | `/api/me` | Get current user |

### Cookie

- **Name**: `session_id`
- **Options**: `HttpOnly`, `Secure`, `SameSite=lax`
- **Max-Age**: 86400 (24 hours)

---

## Error Handling

Common error responses:

| Status | Detail | Action |
|--------|--------|--------|
| 401 | Invalid credentials | Show error message |
| 401 | Invalid or expired session | Redirect to login |
| 429 | Too Many Requests | Show rate limit message |

---

## Notes

1. **Credentials**: Always use query parameters for login (not JSON body)
2. **Cookies**: Are automatically handled by browsers since `HttpOnly` and `Secure` are set
3. **Server vs Client**: Prefer server-side auth checks for protected routes
4. **Keycloak**: Token revocation happens on logout (refresh token is invalidated)