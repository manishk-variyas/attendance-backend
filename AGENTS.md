# AGENTS.md

Guidance for AI agents and developers working in this repo.

## Read this first
The `context/` folder is the source of truth. Read the relevant file before
changing code:
- `context/architecture.md` — services, network, data flow
- `context/data-model.md` — tables, enums, key columns
- `context/analytics.md` — attendance/tickets/leaves/WFH analytics rules
- `context/auth-and-sessions.md` — login, sessions, rate limiting, brute force
- `context/conventions.md` — code style and patterns
- `context/gotchas.md` — known traps (read before debugging)
- `context/runbook.md` — build, run, test, DB access

## Golden rules
- Do not guess about behaviour — verify against the code and the live DB.
- Prefer the existing patterns in `apps/backend/app/features/*`.
- Do not add comments unless asked. Keep changes small and tested.
- Only commit when explicitly asked.
- After backend changes: `cd infra && docker compose build backend && docker compose up -d backend`, then check `http://localhost/health`.

## Layout
- `apps/backend/` — FastAPI backend (the main app)
- `infra/` — docker compose, nginx, keycloak, redmine, pgbouncer configs
- `docs/` — API references and feature docs
