# Architecture

## Stack
- **Backend**: FastAPI (`apps/backend/app`), SQLAlchemy, served by gunicorn +
  uvicorn workers (`Dockerfile` CMD). Entry: `app.main:app`.
- **DB**: PostgreSQL. Backend connects through **pgbouncer** (transaction mode).
- **Auth**: Keycloak (OIDC). Sessions stored server-side (table `sessions`).
- **Redmine**: separate app with its own local DB (issues, projects, users).
- **nginx**: single entry point for the API.
- **Other**: minio (storage), redmine-db, keycloak-db.

## Services & ports
| Service | Container | Reachable at |
|---|---|---|
| nginx | `infra-nginx-1` | `0.0.0.0:80` (public entry) |
| backend | `infra-backend-1` | `backend:8000` (internal only, no published port) |
| keycloak | `infra-keycloak-1` | `127.0.0.1:8080` |
| redmine | `infra-redmine-1` | `127.0.0.1:3000` |
| pgbouncer | `infra-pgbouncer-1` | `127.0.0.1:6432` |
| postgres | `backend_postgres` | `192.168.122.101:5432` |
| grafana | `infra-grafana` | `127.0.0.1:3001` |
| prometheus | `infra-prometheus` | `127.0.0.1:9090` |
| tempo | `infra-tempo` | `127.0.0.1:3200` (internal 4317/4318) |

## Request flow (API)
```
browser → frontend/proxy (192.168.122.1) → nginx:80 → backend:8000 → pgbouncer → postgres
```
- The backend is **only reachable through nginx** — it has no published port.
- nginx sets `X-Real-IP`/`X-Forwarded-For`; the backend trusts them (see
  `auth-and-sessions.md`).

## Backend feature layout
`app/features/<feature>/` — each has `routes.py`, `schemas.py`, `service.py`
(and sometimes `services/`). Features: `auth`, `attendance`, `leaves`, `wfh`,
`shifts`, `analytics`, `employees`, `redmine`, `admin`, `audit`, `location`,
`notifications`, `settings`.
