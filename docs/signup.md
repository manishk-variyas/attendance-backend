# Signup Endpoint Implementation

## Overview

The signup endpoint allows new users to register themselves in Keycloak through the backend. The backend calls Keycloak's Admin REST API using a service account.

---

## Prerequisites

### 1. Enable Service Accounts on Backend Client

```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh update clients/backend-client \
  -r attendance-app -s serviceAccountsEnabled=true
```

### 2. Assign Realm-Admin Role to Service Account

```bash
# Get the service account user ID
SERVICE_ACCOUNT_UID=$(docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh get \
  clients/backend-client/service-account-user -r attendance-app | jq -r '.id')

# Get the realm-admin role ID
REALM_ADMIN_ROLE_ID=$(docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh get \
  roles/realm-admin -r attendance-app | jq -r '.id')

# Assign the role
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh add-roles \
  -r attendance-app --uid "$SERVICE_ACCOUNT_UID" --role-id "$REALM_ADMIN_ROLE_ID"
```

---

## Signup Flow

```
┌──────────┐     POST /auth/signup      ┌──────────┐
│          │ ─────────────────────────► │          │
│  Browser │   {                       │ Backend  │
│          │     username,             │          │
│          │     email,                │  - Get   │
│          │     password              │    admin │
│          │   }                       │    token │
└──────────┘                          └────┬────┘
                                             │
                                             ▼
                                      ┌──────────┐
                                      │          │
                                      │Keycloak  │
                                      │   Admin  │
                                      │   API    │
                                      │          │
                                      │  - Create│
                                      │    user  │
                                      │  - Set   │
                                      │  password│
                                      └──────────┘
```

---

## Request Format

### Frontend Sends to Backend

```json
POST /auth/signup
Content-Type: application/json

{
  "username": "johndoe",
  "email": "john@example.com",
  "password": "SecurePass123!"
}
```

### Password Requirements

- **Minimum 8 characters**
- **At least one uppercase letter**
- **At least one number**
- **At least one special character** (!@#$%^&*)

> Keycloak handles password hashing internally - you do NOT hash the password before sending it to the backend.

---

## Backend Configuration

### Environment Variables

Add to `apps/backend/.env`:

```env
# Keycloak Admin API (for user creation)
KEYCLOAK_ADMIN_CLIENT_ID=backend-client
KEYCLOAK_ADMIN_CLIENT_SECRET=best-practice-secret-12345
KEYCLOAK_ADMIN_URL=http://keycloak:8080/admin
```

---

## API Calls Made by Backend

### Step 1: Get Admin Token

```bash
POST http://keycloak:8080/realms/attendance-app/protocol/openid-connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
client_id=backend-client
client_secret=best-practice-secret-12345
```

**Response:**

```json
{
  "access_token": "eyJ...",
  "expires_in": 300,
  "token_type": "Bearer"
}
```

### Step 2: Create User

```bash
POST http://keycloak:8080/admin/realms/attendance-app/users
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "username": "johndoe",
  "email": "john@example.com",
  "enabled": true,
  "emailVerified": false
}
```

### Step 3: Set Password

```bash
PUT http://keycloak:8080/admin/realms/attendance-app/users/{userId}/reset-password
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "type": "password",
  "value": "SecurePass123!",
  "temporary": false
}
```

---

## Signup Endpoint Response

### Success (201 Created)

```json
{
  "message": "User created successfully",
  "user_id": "abc-123-def"
}
```

### Error - Username Taken (409 Conflict)

```json
{
  "detail": "Username already exists"
}
```

### Error - Email Taken (409 Conflict)

```json
{
  "detail": "Email already exists"
}
```

### Error - Invalid Password (400 Bad Request)

```json
{
  "detail": "Password does not meet requirements"
}
```

---

## Security Considerations

1. **Rate limiting** - Apply stricter rate limit to signup (e.g., 5/minute per IP)
2. **Email verification** - Set `emailVerified: false` initially, then send verification email
3. **Password validation** - Validate password format BEFORE sending to Keycloak
4. **Service account** - Only use realm-admin role during development; create a custom "user creator" role for production
5. **HTTPS** - Always use HTTPS in production; never send passwords over HTTP

---

## Code Changes

### 1. Add Environment Variables

**File:** `apps/backend/app/core/config.py`

```python
# Add after KEYCLOAK_CLIENT_SECRET
KEYCLOAK_ADMIN_URL: str = "http://keycloak:8080/admin"
```

---

### 2. Add Signup Schema

**File:** `apps/backend/app/schemas/auth.py`

```python
from pydantic import BaseModel, EmailStr, field_validator

class SignupRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one number")
        if not re.search(r"[!@#$%^&*]", v):
            raise ValueError("Password must contain at least one special character (!@#$%^&*)")
        return v


class SignupResponse(BaseModel):
    message: str
    user_id: str
```

---

### 3. Add Keycloak Admin Functions

**File:** `apps/backend/app/services/keycloak.py`

```python
async def get_admin_token() -> str:
    """Get admin access token using client credentials."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.KEYCLOAK_URL}/realms/{settings.REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to get admin token")
    return response.json()["access_token"]


async def create_keycloak_user(username: str, email: str) -> str:
    """Create a user in Keycloak. Returns user ID."""
    admin_token = await get_admin_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.KEYCLOAK_ADMIN_URL}/realms/{settings.REALM}/users",
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )

    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="Username already exists")
    if response.status_code == 400 and "email" in response.text:
        raise HTTPException(status_code=409, detail="Email already exists")

    if response.status_code != 201:
        raise HTTPException(status_code=500, detail="Failed to create user")

    # Extract user ID from Location header
    user_id = response.headers["Location"].split("/")[-1]
    return user_id


async def set_keycloak_password(user_id: str, password: str) -> bool:
    """Set password for a user in Keycloak."""
    admin_token = await get_admin_token()

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{settings.KEYCLOAK_ADMIN_URL}/realms/{settings.REALM}/users/{user_id}/reset-password",
            json={
                "type": "password",
                "value": password,
                "temporary": False,
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )

    return response.status_code == 204
```

---

### 4. Add Signup Route

**File:** `apps/backend/app/auth/routes.py`

```python
from app.schemas.auth import SignupRequest, SignupResponse
from app.services.keycloak import create_keycloak_user, set_keycloak_password

@router.post("/signup")
@limiter.limit("5/minute")
async def signup(request: Request, signup_data: SignupRequest):
    """Signup endpoint - create a new user in Keycloak."""
    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"

    try:
        # Create user in Keycloak
        user_id = await create_keycloak_user(signup_data.username, signup_data.email)

        # Set password
        await set_keycloak_password(user_id, signup_data.password)

        log_security_event(
            correlation_id,
            "signup",
            f"User {signup_data.username} created",
            severity="info",
            extra={"username": signup_data.username, "client_ip": client_ip},
        )

        return SignupResponse(
            message="User created successfully",
            user_id=user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {e}")
        log_security_event(
            correlation_id,
            "signup",
            f"Signup failed: {str(e)}",
            severity="error",
        )
        raise HTTPException(status_code=500, detail="Failed to create user")
```

---

### 5. Files to Modify

| File | Changes |
|------|---------|
| `apps/backend/app/core/config.py` | Add `KEYCLOAK_ADMIN_URL` setting |
| `apps/backend/app/schemas/auth.py` | Add `SignupRequest`, `SignupResponse` schemas |
| `apps/backend/app/services/keycloak.py` | Add `get_admin_token`, `create_keycloak_user`, `set_keycloak_password` |
| `apps/backend/app/auth/routes.py` | Add `/signup` endpoint |