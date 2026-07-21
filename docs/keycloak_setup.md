# Keycloak Setup (Simple Guide)

This guide shows you how to set up Keycloak for our app. We use the **`attendance-app`** realm (separate from the `master` realm, which is only for admin).

---

## What you need to do
1.  **Start the services** (`nerdctl compose up -d`).
2.  **Wait for Keycloak to be healthy** (it takes ~60 seconds on a small VM).
3.  **Create the `attendance-app` realm**.
4.  **Create a Client** (the connection between our backend and Keycloak).
5.  **Disable Verify Profile** (required for ROPC/password grant login).
6.  **Create a User** (a test person with a password).

---

## Step 1: Login to the Keycloak Admin CLI
Run this command first to authenticate so we can make changes:
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password admin
```

---

## Step 2: Create the `attendance-app` Realm
Our app uses its own isolated realm, separate from the Keycloak `master` realm:
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create realms \
  -s realm=attendance-app -s enabled=true
```

---

## Step 3: Create the Backend Client
This creates the "phone line" between our backend and Keycloak:
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create clients -r attendance-app \
  -s clientId=backend-client \
  -s enabled=true \
  -s publicClient=false \
  -s secret=best-practice-secret-12345 \
  -s directAccessGrantsEnabled=true \
  -s "redirectUris=[\"*\"]" \
  -s "webOrigins=[\"*\"]" \
  -s "backchannelLogoutUrl=http://backend:8000/auth/backchannel-logout" \
  -s backchannelLogoutSessionRequired=true \
  -s backchannelLogoutRevokeOfflineSessions=true
```

### What did we just do?
- **clientId**: The name our backend uses to find Keycloak.
- **secret**: Must match `KEYCLOAK_CLIENT_SECRET` in `apps/backend/.env`.
- **directAccessGrantsEnabled**: Allows the backend to send passwords directly (ROPC flow).
- **backchannelLogoutUrl**: Tells Keycloak to notify backend when user logs out from Keycloak admin or another client.
- **backchannelLogoutSessionRequired**: Requires session tracking for backchannel logout.
- **backchannelLogoutRevokeOfflineSessions**: Revokes offline tokens on logout.

---

## Step 4: Disable "Verify Profile" Required Action
> **IMPORTANT**: Keycloak 24.x enables "Verify Profile" by default. This blocks
> ROPC (password grant) logins because there's no UI to verify the profile.
> We MUST disable it for our BFF pattern to work.

```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh update \
  authentication/required-actions/VERIFY_PROFILE -r attendance-app -s enabled=false
```

---

## Step 5: Create a Test User
**A. Create the user:**
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create users -r attendance-app \
  -s username=testuser -s enabled=true
```

**B. Set their password:**
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh set-password -r attendance-app \
  --username testuser --new-password password --temporary=false
```

---

## Step 6: Test it!
Test the full auth flow through the backend API:

**Login:**
```bash
curl -i -X POST "http://localhost/auth/login?username=testuser&password=password" -c cookies.txt
```

**Get Profile:**
```bash
curl -i -X GET http://localhost/api/me -b cookies.txt
```

**Refresh Session:**
```bash
curl -i -X POST http://localhost/auth/refresh -b cookies.txt -c cookies.txt
```

**Logout:**
```bash
curl -i -X POST http://localhost/auth/logout -b cookies.txt
```

**Test Backchannel Logout (simulate Keycloak admin logout):**
```bash
curl -i -X POST http://localhost/auth/backchannel-logout \
  -H "Content-Type: application/json" \
  -d '{"logout_token":"<logout-token-from-keycloak>"}'
```

---

## All-in-One Setup Script
After `nerdctl compose up -d`, wait for Keycloak health, then run:
```bash
# 1. Login to admin CLI
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password admin

# 2. Create realm
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create realms \
  -s realm=attendance-app -s enabled=true

# 3. Create client with backchannel logout
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create clients -r attendance-app \
  -s clientId=backend-client -s enabled=true -s publicClient=false \
  -s secret=best-practice-secret-12345 -s directAccessGrantsEnabled=true \
  -s "redirectUris=[\"*\"]" -s "webOrigins=[\"*\"]" \
  -s "backchannelLogoutUrl=http://backend:8000/auth/backchannel-logout" \
  -s backchannelLogoutSessionRequired=true \
  -s backchannelLogoutRevokeOfflineSessions=true

# 4. Disable Verify Profile (CRITICAL for ROPC login!)
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh update \
  authentication/required-actions/VERIFY_PROFILE -r attendance-app -s enabled=false

# 5. Create test user
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh create users -r attendance-app \
  -s username=testuser -s enabled=true

# 6. Set password
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh set-password -r attendance-app \
  --username testuser --new-password password --temporary=false
```

---

## Using the Keycloak Web Console
Go to: `http://localhost:8080/admin/`
- **Username**: `admin`
- **Password**: `admin`

Select the **`attendance-app`** realm from the dropdown to manage clients and users.
