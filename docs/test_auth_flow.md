# Auth Flow Testing Guide

Complete curl commands to test every part of the authentication system.

---

## Setup

All commands assume the backend is accessible at `http://localhost`. Change to your server URL if different.

```bash
export API="http://localhost"
export COOKIES="/tmp/cookies.txt"
```

---

## 1. Health Check

Verify the backend and Nginx are running.

```bash
curl -s ${API}/health | jq
```

**Expected:**

```json
{
  "status": "ok"
}
```

---

## 2. Login

Authenticate with valid credentials.

```bash
curl -s -i -X POST "${API}/auth/login?username=testuser&password=password" \
  -c ${COOKIES}
```

**Expected — 200 OK:**

```
HTTP/1.1 200 OK
set-cookie: session_id=...; HttpOnly; Path=/; SameSite=lax
{"message":"Login successful","user":{"sub":"...","username":"testuser","email":null,"roles":["default-roles-attendance-app","offline_access","uma_authorization"]}}
```

### 2a. Login with wrong password

```bash
curl -s -X POST "${API}/auth/login?username=testuser&password=wrong"
```

**Expected — 401:**

```json
{"detail":"Invalid credentials"}
```

### 2b. Login with missing user

```bash
curl -s -X POST "${API}/auth/login?username=nonexistent&password=test"
```

**Expected — 401:**

```json
{"detail":"Invalid credentials"}
```

---

## 3. Get Current User

Access a protected endpoint. The cookie is sent automatically from the file.

```bash
curl -s ${API}/api/me -b ${COOKIES} | jq
```

**Expected — 200 OK:**

```json
{
  "sub": "2a0a2f4c-a07e-4465-9e51-1ef2bc3cc2c6",
  "username": "testuser",
  "email": null,
  "roles": ["default-roles-attendance-app", "offline_access", "uma_authorization"]
}
```

### 3a. Access without cookie

```bash
curl -s -i ${API}/api/me
```

**Expected — 401:**

```
HTTP/1.1 401 Unauthorized
{"detail":"Not authenticated"}
```

### 3b. Access with invalid session ID

```bash
curl -s -i ${API}/api/me -b "session_id=invalid_value_12345"
```

**Expected — 401:**

```
HTTP/1.1 401 Unauthorized
{"detail":"Invalid or expired session"}
```

---

## 4. Session Refresh

Refresh the session and verify session rotation (new session ID issued).

```bash
# Extract the current session ID
OLD_SESSION=$(grep session_id ${COOKIES} | awk '{print $NF}')
echo "Old session: ${OLD_SESSION}"

# Refresh
curl -s -i -X POST "${API}/auth/refresh" \
  -b ${COOKIES} -c ${COOKIES}

# Extract the new session ID
NEW_SESSION=$(grep session_id ${COOKIES} | awk '{print $NF}')
echo "New session: ${NEW_SESSION}"
```

**Expected — 200 OK:**

```
HTTP/1.1 200 OK
set-cookie: session_id=<NEW_ID>; HttpOnly; Path=/; SameSite=lax
{"message":"Session refreshed","user":{"sub":"...","username":"testuser","email":null,"roles":[...]}}
```

### 4a. Verify old session is invalid

```bash
curl -s -i ${API}/api/me -b "session_id=${OLD_SESSION}"
```

**Expected — 401:**

```json
{"detail":"Invalid or expired session"}
```

### 4b. Verify new session works

```bash
curl -s ${API}/api/me -b ${COOKIES} | jq
```

**Expected — 200 OK** with user data.

---

## 5. Logout

Log out from the application.

```bash
curl -s -i -X POST "${API}/auth/logout" -b ${COOKIES}
```

**Expected — 200 OK:**

```
HTTP/1.1 200 OK
set-cookie: session_id=""; expires=...; Max-Age=0; Path=/
{"message":"Logout successful"}
```

### 5a. Verify session is gone

```bash
curl -s -i ${API}/api/me -b ${COOKIES}
```

**Expected — 401:**

```json
{"detail":"Invalid or expired session"}
```

---

## 6. Rate Limiting

Test that login attempts are rate limited (max 5 per minute).

```bash
for i in $(seq 1 8); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${API}/auth/login?username=testuser&password=wrong")
  echo "Attempt $i: HTTP ${STATUS}"
done
```

**Expected:**

```
Attempt 1: HTTP 401
Attempt 2: HTTP 401
Attempt 3: HTTP 401
Attempt 4: HTTP 429
Attempt 5: HTTP 429
...
```

Wait 60 seconds for the rate limit window to reset.

---

## 7. Correlation ID

Test end-to-end request tracing.

```bash
curl -s -i -X POST "${API}/auth/login?username=testuser&password=password" \
  -H "X-Correlation-ID: my-test-flow-001" \
  -c ${COOKIES}
```

**Expected — Response includes the same header:**

```
x-correlation-id: my-test-flow-001
```

### View in logs

```bash
# Access log entry
docker exec infra-backend-1 grep "my-test-flow-001" /app/logs/access.log

# Audit log entry
docker exec infra-backend-1 grep "my-test-flow-001" /app/logs/audit.log
```

---

## 8. Full Flow (All Steps in One Script)

Copy and paste this entire block to run the complete test:

```bash
#!/bin/bash
set -e

API="http://localhost"
COOKIES="/tmp/test-cookies.txt"

echo "=== 1. Health Check ==="
curl -s ${API}/health | jq

echo -e "\n=== 2. Login ==="
curl -s -i -X POST "${API}/auth/login?username=testuser&password=password" \
  -c ${COOKIES}

echo -e "\n=== 3. Get User Profile ==="
curl -s ${API}/api/me -b ${COOKIES} | jq

echo -e "\n=== 4. Refresh Session ==="
curl -s -i -X POST "${API}/auth/refresh" \
  -b ${COOKIES} -c ${COOKIES}

echo -e "\n=== 5. Verify Profile After Refresh ==="
curl -s ${API}/api/me -b ${COOKIES} | jq

echo -e "\n=== 6. Logout ==="
curl -s -i -X POST "${API}/auth/logout" -b ${COOKIES}

echo -e "\n=== 7. Verify Logged Out ==="
curl -s -i ${API}/api/me -b ${COOKIES}

rm -f ${COOKIES}
echo -e "\n=== All tests passed ==="
```

---

## 9. Test Backchannel Logout

Simulates Keycloak notifying the backend that a user was logged out.

### Step 1: Login and note session

```bash
curl -s -X POST "${API}/auth/login?username=testuser&password=password" \
  -c ${COOKIES} | jq
```

### Step 2: Generate a test logout token

The backchannel logout endpoint expects a JWT with the `events` claim. For testing, you can create one:

```bash
# Install python-jose if not available
pip install python-jose

python3 -c "
from jose import jwt
import json

payload = {
    'sub': '2a0a2f4c-a07e-4465-9e51-1ef2bc3cc2c6',
    'events': {'http://schemas.openid.net/event/backchannel-logout': {}},
    'iat': 1700000000,
    'exp': 1700003600,
}

# Use the same secret as the backend
token = jwt.encode(payload, 'best-practice-secret-12345', algorithm='HS256')
print(token)
" > /tmp/logout_token.txt

echo "Logout token: $(cat /tmp/logout_token.txt)"
```

### Step 3: Send backchannel logout request

```bash
curl -s -i -X POST "${API}/auth/backchannel-logout" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "logout_token=$(cat /tmp/logout_token.txt)"
```

**Expected — 204 No Content:**

```
HTTP/1.1 204 No Content
```

### Step 4: Verify session was deleted

```bash
curl -s -i ${API}/api/me -b ${COOKIES}
```

**Expected — 401:**

```json
{"detail":"Invalid or expired session"}
```

### Check audit log

```bash
docker exec infra-backend-1 grep "backchannel_logout" /app/logs/audit.log
```

**Expected:**

```json
{"time":"...","level":"INFO","source":"app.audit","message":"Backchannel logout: user 2a0a2f4c-... (1 session deleted)","metadata":{"action":"backchannel_logout","user_sub":"2a0a2f4c-...","sessions_deleted":1,"source":"keycloak"}}
```

---

## 10. Test Log Files

### View access log (traffic)

```bash
docker exec infra-backend-1 cat /app/logs/access.log | jq
```

### View audit log (security events)

```bash
docker exec infra-backend-1 cat /app/logs/audit.log | jq
```

### View error log (errors)

```bash
docker exec infra-backend-1 cat /app/logs/error.log
```

### Check log rotation

```bash
docker exec infra-backend-1 ls -lh /app/logs/
```

**Expected:**

```
-rw-r--r-- 1 root root  563 Apr 30 06:56 access.log
-rw-r--r-- 1 root root  333 Apr 30 06:56 audit.log
-rw-r--r-- 1 root root    0 Apr 30 06:56 error.log
```

---

## 11. Verify Cron Jobs

```bash
docker exec infra-backend-1 crontab -l
```

**Expected:**

```
0 * * * * find /app/logs -name "*.log" -size +50M -delete 2>/dev/null
0 0 * * * find /app/logs -name "*.log.*" -mtime +14 -delete 2>/dev/null
```

---

## Quick Reference

| Test | Command | Expected |
|------|---------|----------|
| Health | `curl ${API}/health` | `{"status":"ok"}` |
| Login | `curl -X POST "${API}/auth/login?..." -c cookies` | 200 + cookie |
| Profile | `curl ${API}/api/me -b cookies` | 200 + user |
| Refresh | `curl -X POST ${API}/auth/refresh -b cookies -c cookies` | 200 + new cookie |
| Logout | `curl -X POST ${API}/auth/logout -b cookies` | 200 + cookie cleared |
| No auth | `curl ${API}/api/me` | 401 |
| Wrong password | `curl -X POST "${API}/auth/login?..."` | 401 |
| Rate limit | Run login 8 times rapidly | 429 after ~3 |
