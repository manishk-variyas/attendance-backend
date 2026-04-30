# Migration & Architecture Setup Guide

Complete guide for deploying this project on a new VM or server.

---

## 1. Architecture Overview

```
External User
     |
     v
  Port 80 (HTTP)
     |
     v
+----------------+
|     Nginx      |  <- Only public-facing service
|   (Port 80)    |
+-------+--------+
        |
        | internal network (infra-network)
        v
+----------------+     +----------------+     +----------------+
|   Backend      |---->|    Keycloak    |---->|   Postgres     |
|   (FastAPI)    |     |    (Auth)      |     |   (Keycloak DB)|
|   :8000        |     |   :8080        |     |   :5432        |
+-------+--------+     +----------------+     +----------------+
        |
        v
+----------------+
|    Redis       |
|   :6379        |
+----------------+
```

### Network isolation
- **Nginx (Port 80)** — Only service exposed to the outside world
- **Keycloak (127.0.0.1:8080)** — Not accessible from outside, only internal calls from backend
- **Redis (127.0.0.1:6379)** — Not accessible from outside, only internal calls from backend
- **Postgres** — No external port at all

### Auth flow summary
User → Nginx → Backend → Keycloak → Redis sessions → Backend → Nginx → User

---

## 2. Prerequisites

- Linux VM (Ubuntu 24.04 recommended)
- Docker installed (`apt install docker.io docker-compose-v2`)
- `curl` installed
- At least 2 GB RAM, 10 GB disk
- Ports available: `80`, `127.0.0.1:8080`, `127.0.0.1:6379`

### Verify Docker is installed

```bash
docker --version
docker compose version
```

---

## 3. Step-by-Step Setup

### Step 1: Clone the project

```bash
git clone <your-repo-url> /opt/backend-monorepo
cd /opt/backend-monorepo
```

### Step 2: Configure environment variables

Edit `apps/backend/.env` with your values:

```bash
nano apps/backend/.env
```

Minimum required changes:

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Random 64-char hex for session security | Change this! |
| `CORS_ORIGINS` | Your frontend URL | `["http://localhost:5173","http://localhost:3000"]` |
| `COOKIE_SECURE` | Set `True` when behind HTTPS | `False` (dev only) |

**Generate a secret key:**

```bash
openssl rand -hex 32
```

### Step 3: Build and start all services

```bash
cd infra
docker compose up -d --build
```

This builds 4 images and starts 5 containers:
- `infra-keycloak-db-1` (Postgres)
- `infra-keycloak-1` (Keycloak)
- `infra-redis-1` (Redis)
- `infra-backend-1` (FastAPI)
- `infra-nginx-1` (Nginx)

### Step 4: Wait for Keycloak to be ready

Keycloak takes 30–60 seconds to start on first boot. Wait:

```bash
sleep 45
```

Verify all containers are running:

```bash
docker ps
```

### Step 5: Set up Keycloak realm and client

Run these commands inside the Keycloak container:

```bash
# Authenticate as admin
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password admin

# Create the application realm
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh create realms \
  -s realm=attendance-app -s enabled=true

# Create the backend client with backchannel logout
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh create clients -r attendance-app \
  -s clientId=backend-client \
  -s enabled=true \
  -s publicClient=false \
  -s secret=best-practice-secret-12345 \
  -s directAccessGrantsEnabled=true \
  -s 'redirectUris=["*"]' \
  -s 'webOrigins=["*"]' \
  -s 'attributes={"backchannel.logout.url":"http://backend:8000/auth/backchannel-logout","backchannel.logout.revoke.offline.tokens":"true","backchannel.logout.session.required":"true"}'

# Disable VERIFY_PROFILE (required for ROPC login to work)
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh update \
  authentication/required-actions/VERIFY_PROFILE -r attendance-app -s enabled=false
```

### Step 6: Create a test user

```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh create users -r attendance-app \
  -s username=testuser -s enabled=true

docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh set-password -r attendance-app \
  --username testuser --new-password password --temporary=false
```

### Step 7: Test the full flow

```bash
# Health check
curl http://localhost/health

# Login
curl -X POST "http://localhost/auth/login?username=testuser&password=password" -c /tmp/cookies.txt

# Get profile (uses cookie)
curl http://localhost/api/me -b /tmp/cookies.txt

# Refresh session
curl -X POST http://localhost/auth/refresh -b /tmp/cookies.txt -c /tmp/cookies.txt

# Logout
curl -X POST http://localhost/auth/logout -b /tmp/cookies.txt
```

---

## 4. Configuration Reference

### All environment variables (apps/backend/.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `KEYCLOAK_URL` | `http://keycloak:8080` | Internal Keycloak URL (must match docker service name) |
| `REALM` | `attendance-app` | Keycloak realm name |
| `KEYCLOAK_CLIENT_ID` | `backend-client` | Client ID configured in Keycloak |
| `KEYCLOAK_CLIENT_SECRET` | `best-practice-secret-12345` | Client secret configured in Keycloak |
| `DATABASE_URL` | `postgresql://keycloak:keycloak@keycloak-db:5432/keycloak` | Postgres connection (used by SQLAlchemy) |
| `REDIS_URL` | `redis://redis:6379` | Redis connection |
| `SECRET_KEY` | *(change this)* | 64-char hex string for session security |
| `SESSION_EXPIRE_HOURS` | `24` | Session TTL in hours |
| `BACKEND_URL` | `http://localhost:8000` | Backend URL for backchannel callbacks |
| `FRONTEND_URL` | `http://localhost:5173` | Frontend URL for CORS |
| `COOKIE_SECURE` | `False` | Set `True` when using HTTPS |
| `CORS_ORIGINS` | `["http://localhost:5173","http://localhost:3000"]` | Allowed frontend origins (JSON array) |
| `RATE_LIMIT_PER_SECOND` | `10` | Max requests per second per IP |
| `LOG_DIR` | `logs` | Directory for log files inside the container |

### Keycloak admin console

Access: `http://<vm-ip>:8080/admin/`
- Username: `admin`
- Password: `admin`

---

## 5. Folder Structure

```
backend-monorepo/
├── apps/
│   └── backend/
│       ├── app/                    # Python application code
│       │   ├── core/               # Config, database, logging, lifespan, limiter
│       │   ├── middleware/         # Correlation ID, request logging
│       │   ├── models/             # SQLAlchemy ORM models
│       │   ├── schemas/            # Pydantic request/response schemas
│       │   ├── services/           # Redis session, Keycloak HTTP calls
│       │   ├── auth/               # Auth routes, dependencies
│       │   ├── utils/              # JWT parsing, audit logging
│       │   └── main.py             # FastAPI app entry point
│       ├── scripts/
│       │   └── log-cleanup.cron    # Log rotation cron jobs
│       ├── .env                    # Environment variables (NOT committed)
│       ├── .env.example            # Template for .env
│       ├── Dockerfile
│       ├── pyproject.toml          # Python dependencies
│       └── uv.lock                 # Locked dependency versions
│
├── infra/
│   ├── docker-compose.yml          # Root compose: starts all services
│   ├── .env                        # Postgres + Keycloak admin credentials
│   ├── backend/
│   │   ├── .env                    # Backend env vars (overrides in compose)
│   │   ├── Dockerfile
│   │   └── docker-compose.yml      # Backend-only compose (standalone)
│   ├── keycloak/
│   │   ├── .env
│   │   ├── Dockerfile
│   │   └── docker-compose.yml
│   ├── nginx/
│   │   ├── .env
│   │   ├── Dockerfile
│   │   ├── conf.d/default.conf     # Nginx proxy config
│   │   └── docker-compose.yml
│   └── redis/
│       ├── .env
│       ├── Dockerfile
│       └── docker-compose.yml
│
└── docs/
    ├── migration.md                # This file
    ├── auth_flow.md                # Auth flow explanation
    └── frontend_api_setup.md       # Frontend integration guide
```

---

## 6. Operations

### Start all services
```bash
cd infra && docker compose up -d
```

### Stop all services
```bash
cd infra && docker compose down
```

### View logs
```bash
# Docker container logs
docker logs infra-backend-1 -f

# Application log files (inside backend container)
docker exec infra-backend-1 tail -f /app/logs/access.log
docker exec infra-backend-1 tail -f /app/logs/audit.log
docker exec infra-backend-1 tail -f /app/logs/error.log
```

### Restart a single service
```bash
docker compose -f infra/docker-compose.yml restart backend
```

### Rebuild and restart
```bash
cd infra && docker compose up -d --build
```

### Check Keycloak realm/client
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh get realms/attendance-app
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh get clients -r attendance-app
```

---

## 7. Troubleshooting

| Problem | Solution |
|---------|----------|
| `502 Bad Gateway` on login | Check `docker logs infra-backend-1` — backend may have crashed |
| `Connection refused` to Keycloak | Keycloak takes 30-60s to start, wait and retry |
| Login returns 401 with valid creds | VERIFY_PROFILE may be enabled, run the disable command |
| Port 80 already in use | Run `sudo ss -tlnp \| grep :80` to find what's using it |
| Backend container restarts in loop | Check `docker logs infra-backend-1` for import errors |
| Nginx can't reach backend | Verify both containers are on `infra-network`: `docker network inspect infra_infra-network` |

---

## 8. Production Checklist

- [ ] Change `SECRET_KEY` to a strong random value
- [ ] Set `COOKIE_SECURE=True` when behind HTTPS
- [ ] Update `CORS_ORIGINS` to your production frontend URL
- [ ] Change `KEYCLOAK_ADMIN_PASSWORD` in `infra/.env`
- [ ] Change `KEYCLOAK_CLIENT_SECRET` in `apps/backend/.env` and Keycloak client config
- [ ] Add TLS/SSL termination (Caddy, Nginx + certbot, or cloud LB)
- [ ] Configure log rotation (cron jobs are already in the Dockerfile)
- [ ] Set up health check monitoring on `http://localhost/health`
- [ ] Back up Postgres volume: `docker run --rm -v infra_keycloak_db_data:/data -v $(pwd):/backup alpine tar czf /backup/keycloak-db-backup.tar.gz -C /data .`
