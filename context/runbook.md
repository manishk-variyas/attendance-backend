# Runbook

## Build & deploy (backend)
```bash
cd infra
docker compose build backend
docker compose up -d backend
curl -s -o /dev/null -w "%{http_code}\n" http://localhost/health   # expect 200
```

## Logs
```bash
docker logs infra-backend-1 --since 10m 2>&1 | grep -iE "error|traceback"
docker logs infra-nginx-1  --since 10m 2>&1 | tail
```

## DB access
Backend connects through pgbouncer; for ad-hoc queries use the creds in
`infra/backend/.env` (`DATABASE_URL`) against `127.0.0.1:6432`.
```bash
# example
psql -h 127.0.0.1 -p 6432 -U <user> -d <db>
```
Use `redmine.` schema prefix for Redmine tables.

## Redmine / Keycloak
- Redmine UI: `http://localhost:3000` (tunnel `ssh -L 3000:localhost:3000 ...`).
- Keycloak: `127.0.0.1:8080`. Realms: `attendance-app` (default), `hepc-tenant`, `VAPT`.

## Monitoring & Observability
- Grafana UI: `http://localhost:3001` (user: `admin`, password: `admin`). Dashboards → System & API Overview.
- Prometheus: `http://localhost:9090`.
- Guide & runbook: `docs/monitoring-guide.md` and `infra/monitoring/README.md`.

## Verifying analytics (independent)
Do not assert the code against itself. Recompute from raw SQL and compare to the
API. Existing scripts (in `/tmp/opencode`, not committed): `verify1..4.py`.
Pattern:
1. Fetch API JSON with a session cookie.
2. Recompute expected values from `psql`.
3. Assert equality (members, totals, daily, streaks, monthly).

Cover: short range, previous month, full year, weekend/holiday shifts, cap.

## Common commands
```bash
# health
curl http://localhost/health
# login (default realm)
curl -X POST http://localhost/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<pw>"}'
# login (vapt realm)
curl -X POST http://localhost/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<pw>","realm":"vapt"}'
```
