# Health Checks for Temporal Integration

Host-side health probe for the attendance stack. It verifies every dependency
end to end and is designed to be called from a **Temporal activity** (or cron,
or CI) without adding any new public API to the backend.

- Script: `scripts/healthcheck.sh`
- Runtime: runs on the Docker host (needs `docker`, `curl`, `bash`; `psql`
  optional for the real DB query)
- No authentication, no new endpoints — it inspects the running containers and
  the already-exposed internal ports.

---

## What it checks

| Check | Target | Severity | Proves |
|---|---|---|---|
| `edge_nginx_backend` | `http://127.0.0.1:80/health` | critical | nginx is up **and** proxying to the backend (`{"status":"ok"}`) |
| `backend_server_time` | `http://127.0.0.1:80/server-time` | critical | backend request path beyond the static route |
| `keycloak_realm` | `http://127.0.0.1:8080/realms/<realm>/.well-known/openid-configuration` | critical | Keycloak up + realm resolvable (login/token will work) |
| `redmine_web` | `http://127.0.0.1:3000/` | critical | Redmine web up |
| `redmine_api` | `http://127.0.0.1:3000/projects.json?key=…&limit=1` | warning | Redmine REST + API key valid |
| `minio_live` | `http://127.0.0.1:9000/minio/health/live` | warning | object storage (logos/assets) reachable |
| `app_db_query` | `psql … SELECT 1` via pgbouncer | critical | app DB reachable **and** queryable through the pooler |
| `db_pgbouncer` | TCP `127.0.0.1:6432` | critical | pgbouncer accepting connections |
| `db_postgres` | `docker exec backend_postgres pg_isready` | critical | app Postgres up |
| `db_redmine` | `docker exec infra-redmine-db-1 pg_isready` | critical | Redmine DB up |
| `db_keycloak` | `docker exec infra-keycloak-db-1 pg_isready` | critical | Keycloak DB up |
| `ct_backend` / `ct_nginx` / `ct_keycloak` / `ct_redmine` | `docker inspect` | critical | containers running |
| `ct_minio` | `docker inspect` | warning | MinIO container running |
| `public_edge` | `https://<domain>/server/health` | critical (when `PUBLIC_URL` set) | TLS + edge proxy + backend through the internet path |

Severity semantics: **critical** failure ⇒ exit `1` and overall `down`.
Non-critical failure ⇒ overall `degraded` but exit `0` (unless `--strict`).

> Note: the backend is served publicly under `/server/` (e.g.
> `https://attendance.variyaslabs.com/server/health`). A bare `/health` at the
> edge is redirected to the login page by the front-end proxy, so the script
> uses the `/server/health` path.

---

## Usage

```bash
# Human-readable table
./scripts/healthcheck.sh

# Machine-readable JSON (for Temporal activities)
./scripts/healthcheck.sh --json

# Fail on warnings too (strict gate)
./scripts/healthcheck.sh --strict

# Include the public/edge check
PUBLIC_URL=https://attendance.variyaslabs.com ./scripts/healthcheck.sh
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | healthy (or degraded with non-critical issues, without `--strict`) |
| `1` | a critical check failed (or any warning under `--strict`) |
| `2` | usage/tooling error |

### Sample JSON output

```json
{
  "status": "ok",
  "checked_at": "2026-09-23T05:48:10Z",
  "checks": [
    {"name": "edge_nginx_backend", "status": "OK", "critical": true, "latency_ms": 21, "detail": "HTTP 200, body ok"},
    {"name": "keycloak_realm", "status": "OK", "critical": true, "latency_ms": 18, "detail": "HTTP 200"},
    {"name": "app_db_query", "status": "OK", "critical": true, "latency_ms": 44, "detail": "SELECT 1 ok via pgbouncer"},
    {"name": "public_edge", "status": "OK", "critical": true, "latency_ms": 73, "detail": "HTTP 200, body ok"}
  ]
}
```

`status` is one of `ok` | `degraded` | `down`. Per-check `status` is
`OK` | `WARN` | `FAIL`.

---

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `HEALTH_HOST` | `127.0.0.1` | host for HTTP/TCP checks |
| `NGINX_PORT` | `80` | edge/nginx port |
| `KEYCLOAK_PORT` | `8080` | Keycloak port |
| `REDMINE_PORT` | `3000` | Redmine port |
| `MINIO_PORT` | `9000` | MinIO port |
| `PGBOUNCER_PORT` | `6432` | pgbouncer port |
| `KC_REALM` | `attendance-app` | realm to validate |
| `PUBLIC_URL` | *(empty)* | enables the public edge check |
| `PUBLIC_HEALTH_PATH` | `/server/health` | public health path |
| `ENV_FILE` | `infra/backend/.env` | source of `DATABASE_URL` / `REDMINE_API_KEY` |
| `HTTP_TIMEOUT` | `5` | per-check timeout (seconds) |
| `BACKEND_CT`, `NGINX_CT`, … | `infra-*` names | container names if they differ |

---

## Temporal integration

Run the script as a **shell activity** on a worker that can reach the Docker
socket/host. Keep the activity thin: call the script, parse JSON, fail the
activity on `down` (or on `degraded` if you want a strict gate).

```python
import json, subprocess
from temporalio import activity

@activity.defn
async def check_stack_health() -> dict:
    proc = subprocess.run(
        ["/home/app-backend/backend-monorepo/scripts/healthcheck.sh", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    result = json.loads(proc.stdout)
    # Fail the activity so the workflow can alert/retry when critical is down.
    if result["status"] == "down":
        failed = [c["name"] for c in result["checks"] if c["status"] == "FAIL"]
        raise ApplicationError(f"stack unhealthy: {failed}", non_retryable=True)
    return result
```

Recommended workflow settings:

| Setting | Value | Why |
|---|---|---|
| Schedule | every 1–5 min | fast detection, low cost |
| `start_to_close_timeout` | 60 s | script has per-check timeouts (default 5 s each) |
| `retry_policy` | 2–3 attempts, backoff 5–30 s | transient blips shouldn't page |
| Alerting | page on `down`; ticket on repeated `degraded` | severity-aware routing |
| Dedup | alert only on state transition (ok→down) | avoid alert storms |

If the Temporal worker runs **inside** the Docker network (not on the host),
`docker exec` and the localhost ports won't be reachable. In that case run the
worker on the host, or set the container/port vars to in-network names and
replace the `docker exec` DB checks with `pg_isready -h <service>` calls.

---

## Remediation map

| Failing check | Likely cause | Action |
|---|---|---|
| `edge_nginx_backend` | nginx down, or backend down | `docker ps`, restart `infra-nginx-1` / `infra-backend-1` |
| `backend_server_time` | backend crashing after boot | check `docker logs infra-backend-1` |
| `keycloak_realm` | Keycloak down / realm deleted | `docker logs infra-keycloak-1`; verify realm exists |
| `redmine_web` / `redmine_api` | Redmine down / bad API key | restart `infra-redmine-1`; refresh `REDMINE_API_KEY` |
| `minio_live` | MinIO down | restart `minio`; check disk |
| `app_db_query` / `db_pgbouncer` | pgbouncer or Postgres down, pool exhausted | restart `infra-pgbouncer-1` / `backend_postgres`; check connections |
| `db_redmine` / `db_keycloak` | their Postgres down | restart the respective DB container |
| `ct_*` | container exited | `docker ps -a`, `docker logs <ct>` |
| `public_edge` | TLS/edge proxy/DNS issue | check edge proxy + cert; verify `/server/health` |
