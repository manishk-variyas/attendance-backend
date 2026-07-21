# API Documentation & Quick Test Guide

This document provides a set of `curl` commands to quickly test the Backend APIs.

## 1. Public Endpoints

### Health Check
Check if the API is alive and connected to Nginx.
```bash
curl -i http://localhost/health
```

### Root Info
```bash
curl -i http://localhost/
```

---

## 2. Authentication Flow

### Login (BFF Pattern)
Authenticates with Keycloak and receives a session cookie.
*   **Username**: `testuser`
*   **Password**: `password`

```bash
curl -i -X POST "http://localhost/auth/login?username=testuser&password=password" -c cookies.txt
```

### Get My Profile
Requires the session cookie obtained from login.
```bash
curl -i -X GET http://localhost/api/me -b cookies.txt
```

### Refresh Session
Refreshes the backend session and the Keycloak tokens.
```bash
curl -i -X POST http://localhost/auth/refresh -b cookies.txt -c cookies.txt
```

### Logout
Invalidates the session in Redis and Revokes the Keycloak token.
```bash
curl -i -X POST http://localhost/auth/logout -b cookies.txt
```

---

## 3. Keycloak Admin (Local Only)
These can only be accessed from the machine where Docker is running.

| Service | Address | Security |
| :--- | :--- | :--- |
| Keycloak UI | `http://localhost:8080/admin` | Bound to 127.0.0.1 (Local Only) |
| Redis | `localhost:6379` | Bound to 127.0.0.1 (Local Only) |

---

## 4. Helpful Management Commands

### Check Container Status
```bash
nerdctl ps -a
```

### View Backend Logs
```bash
nerdctl logs -f keycloak-backend-1
```

### Login to Keycloak CLI (Inside Container)
```bash
nerdctl exec keycloak-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password admin
```
