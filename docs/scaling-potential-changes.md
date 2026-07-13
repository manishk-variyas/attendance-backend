# Scaling to 200–300 Concurrent Users

Current architecture limits and the changes needed to support 200–300 concurrent users, ordered by impact.

---

## 1. Uvicorn Workers — Single Process

**File:** `apps/backend/Dockerfile:53`

Currently runs one Python process. A single async worker can handle ~100 concurrent connections before saturation.

**Change:**
```dockerfile
CMD ["sh", "-c", "cron && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
```

4 workers = 4 processes, each with its own event loop and DB pool.

---

## 2. Database Connection Pool

**File:** `apps/backend/app/core/database.py:22`

SQLAlchemy defaults: `pool_size=5`, `max_overflow=10` = max 15 connections. All requests (sessions, queries) compete for these.

**Change:**
```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
)
```

Also requires PostgreSQL side: add `-c max_connections=200` in `infra/postgres/docker-compose.yml`.

---

## 3. Session Storage: PostgreSQL → Redis

**Files:** `apps/backend/app/features/auth/services/session.py`, `apps/backend/app/core/config.py`

Currently every authenticated request queries PostgreSQL to look up the session (~10–50ms). Redis is in-memory (~1ms) and offloads the DB.

`REDIS_URL` is already defined in `.env` but never used. Implement a Redis-backed session store.

---

## 4. Nginx — Multiple Backend Instances

**File:** `infra/nginx/conf.d/default.conf`

Single upstream `backend:8000` — all traffic goes to one container.

**Change:**
```nginx
upstream backend_cluster {
    server backend:8000;
    server backend:8001;
    server backend:8002;
}
```

Then deploy 3–4 backend replicas in docker-compose. Requires Redis session store (change #3) so any instance can serve any user.

---

## 5. Rate Limits

**File:** `apps/backend/.env:27`

`RATE_LIMIT_PER_SECOND=10` → login rate = `10 // 2 = 5/minute`. For 200–300 users this is too restrictive.

**Change:**
```env
RATE_LIMIT_PER_SECOND=200
```

Or use per-IP rate limiting (already in place via `get_remote_address`) so users don't share one bucket.

---

## 6. Async DB Session Management

**Files:** `apps/backend/app/features/auth/services/session.py` (every function)

Each session function (`create_session`, `get_session`, `delete_session`, etc.) opens and closes its own DB connection via `_db()`:
```python
def _db():
    from app.core.database import SessionLocal
    return SessionLocal()
```

This bypasses FastAPI's connection management. Refactor to accept `db: Session` as a parameter, letting the caller use FastAPI's `Depends(get_db)` — connections get pooled and reused properly.

---

## 7. Keycloak Resources

**File:** `infra/keycloak/docker-compose.yml`

Keycloak handles every login and token refresh. Default JVM heap may be insufficient for 300 users.

**Change:**
```yaml
environment:
  KC_DB_POOL_INITIAL_SIZE: 20
  KC_DB_POOL_MAX_SIZE: 100
  JAVA_OPTS: "-Xms512m -Xmx2g"
```

---

## 8. PostgreSQL Tuning

**File:** `infra/postgres/docker-compose.yml`

Default PostgreSQL config is tuned for small workloads.

**Change:**
```yaml
command: >
  -c max_connections=200
  -c shared_buffers=256MB
  -c effective_cache_size=768MB
  -c work_mem=8MB
```

---

## Impact Summary

| Change | Effort | Impact | Priority |
|---|---|---|---|
| Uvicorn `--workers 4` | 1 line | High | P0 |
| DB pool 20/40 + `max_connections` | 3 lines | High | P0 |
| Sessions → Redis | 1–2 days | High | P0 |
| Fix `_db()` → `Depends(get_db)` | Moderate | High | P0 |
| Nginx multi-upstream | 5 lines | Medium | P1 |
| Bump rate limits | 1 line | Medium | P1 |
| Keycloak JVM/memory | 3 lines | Medium | P1 |
| Postgres `shared_buffers` etc. | 4 lines | Low | P2 |
