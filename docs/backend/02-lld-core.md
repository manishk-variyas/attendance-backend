# 02 — LLD Core: Boot, Middleware, Core Modules

Goal: trace one request from boot to response. Files: `apps/backend/app/main.py`, `app/core/*`, `app/middleware/*`.

## 1. Boot sequence

```mermaid
flowchart TB
    G[gunicorn + uvicorn workers<br/>CMD app.main:app] --> L[lifespan<br/>app/core/lifespan.py]
    L --> DB[init_db<br/>Base.metadata.create_all + _run_migrations]
    DB --> AP[FastAPI app<br/>docs/redoc/openapi disabled]
    AP --> ML[limiter + SlowAPIMiddleware<br/>app.state.limiter]
    AP --> EH[exception handlers<br/>429 / 422-sanitized / 500+correlation_id]
    AP --> CORS[CORS<br/>allow_origins=settings.CORS_ORIGINS]
    AP --> MW[http middleware chain<br/>see §2]
    AP --> RT[include_router × 17<br/>see §4]
```

- `lifespan` runs `init_db()` once per worker boot. The attendance sweeper does **not** run here — it is a standalone cron (`scripts/attendance_sweeper.py`) so it executes once per interval.
- `init_db()` does `create_all` plus idempotent `DO $$ ...` migrations in `app/core/database.py` (new columns, indexes, `leave_types`, `notifications`, `states` seed, FK backfills).

## 2. Middleware chain (order matters)

Registered in `main.py` bottom-up. Execution order per request:

```mermaid
flowchart LR
    R[Request] --> C[set_correlation_id<br/>adds request.state.correlation_id]
    C --> LG[log_requests<br/>access log with correlation_id]
    LG --> AE[active_employee_middleware<br/>blocks deactivated accounts globally]
    AE --> Route[Route + Depends: get_db, get_current_user, limiter]
```

| Middleware | File | What it does |
|---|---|---|
| `set_correlation_id` | `app/middleware/correlation.py` | Assigns ID, returned on 500s for log correlation |
| `log_requests` | `app/middleware/logging.py` | Structured request/response log |
| `active_employee_middleware` | `app/middleware/active_employee.py` | Rejects `is_active=false` before any route |
| `origin_validation_middleware` | `app/middleware/csrf.py` | CSRF origin check — currently **commented out** in `main.py` |

## 3. Core modules — what each file owns

| File | Owns |
|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings): env → `.env` → defaults. Computed `JWKS_URL`, `ISSUER_URL`, `TOKEN_URL`, `LOGOUT_URL`. CORS accepts JSON list or comma string. |
| `app/core/database.py` | `engine` (`NullPool` default behind pgbouncer; `queue` only for direct PG), `SessionLocal`, `get_db()` yield/close dependency, `_run_migrations()` |
| `app/core/lifespan.py` | Startup/shutdown hooks |
| `app/core/client_ip.py` | `security_client_ip()`: `X-Real-IP` → leftmost `X-Forwarded-For` → TCP peer. Only safe because nginx is the sole entry. |
| `app/core/limiter.py` | slowapi `limiter`, keyed on real client IP |
| `app/core/logging.py` | `setup_logging()` |
| `app/core/cache.py` | Shared cache helpers |
| `app/core/models.py` | `Base` for ORM models |

## 4. Router registration (`main.py`)

All routers mount under `/api` except auth (own prefixes) and location (`/api/location`):

```mermaid
flowchart TB
    Main[app.main:app] --> Auth[auth]
    Main --> RM[redmine /api]
    Main --> LV[leaves /api]
    Main --> LT[leave-types, leave-balances /api]
    Main --> Geo[countries, states /api]
    Main --> Loc[location /api/location + admin-office-locations /api]
    Main --> SH[shifts /api]
    Main --> WFH[wfh /api]
    Main --> Set[settings /api]
    Main --> Emp[employees /api]
    Main --> Att[attendance + admin-attendance /api]
    Main --> Aud[audit + admin-sessions /api]
    Main --> Not[notifications /api]
    Main --> Ana[analytics /api]
```

## 5. Request lifecycle (code path)

```mermaid
sequenceDiagram
    participant F as FastAPI
    participant MW as Middleware chain
    participant Dep as Depends: get_db + get_current_user
    participant Rt as routes.py (thin)
    participant Sv as service.py (logic)
    participant Db as postgres via pgbouncer

    F->>MW: correlation → logging → active-employee
    MW->>Dep: open SessionLocal, validate session cookie
    Dep-->>Rt: db: Session, current_user: dict
    Rt->>Sv: call with validated inputs
    Sv->>Db: SQLAlchemy ORM or text(...) with bound params
    Db-->>Sv: rows
    Sv-->>Rt: dict / Pydantic model
    Rt-->>F: JSON response
    F->>Dep: db.close()
```

Conventions (enforced):

- Routes stay thin; logic in `service.py`. Pydantic models in `schemas.py`, snake_case.
- DB: `Session` via `Depends(get_db)`; raw SQL uses `text(...)` + bound params, never f-string user input.
- Errors: `raise HTTPException` with short `detail`. Validation errors sanitized to `{field, message}`.
- Dates `YYYY-MM-DD`; day boundaries in **IST**. `ORDER BY` deterministic (tiebreak column). Avoid N+1 — batch with `ANY(:ids)`.

Next: `03-auth-security.md` — login, sessions, limits, blocks.
