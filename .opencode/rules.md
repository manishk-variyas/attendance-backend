# opencode Rules

## Behavioral Rules

### Git
- **NEVER** run destructive or state-changing git commands without explicit user consent. This includes: `commit`, `push`, `amend`, `rebase`, `reset`, `stash`, `tag`.
- Read-only commands (`status`, `log`, `diff`) are allowed without asking.
- Never skip hooks (`--no-verify`, `--no-gpg-sign`) unless the user explicitly requests it.
- Never force push to `main`/`master`.

### File Modifications
- **NEVER edit or write files without explicit user consent.** Before any edit, explain what you intend to change and wait for user confirmation.
- Reading files is always fine.
- When proposing changes, always mention the specific files and line numbers affected.

### Package Management
- Never run `pnpm install`, `uv add`, `uv sync`, `uv pip install`, `npm install`, or similar without asking.
- Never modify `pyproject.toml` dependencies or `package.json` without asking.

### Infrastructure
- Never start, stop, or restart Docker containers (`docker-compose up/down`, `docker run`, etc.) without asking.
- Never modify infrastructure configs (`infra/`, `docker-compose.yml`, `Dockerfile`) without consent.

### Explanation Requirement
- Before running ANY command that modifies system state (file writes, package installs, Docker, git mutations), explain:
  1. What the command does
  2. Why it's needed
  3. What the expected outcome is

### Environment Files
- You may read `.env` files for context, but **NEVER suggest or output** any secret values, API keys, tokens, or passwords found within them.
- Never add `.env` files to git. Never suggest adding secrets to any file.

## Codebase Conventions

### Project Architecture
This is an **attendance system backend monorepo** using:
- **pnpm** workspace manager + **Turbo** for orchestration
- **Python 3.14+** with **FastAPI** as the web framework
- **PostgreSQL** (via SQLAlchemy 2.0 ORM) + **Redis** (sessions/cache)
- **Keycloak** for authentication (OIDC)

### Directory Structure
```
apps/backend/app/
├── core/            # Config, database, lifecycle, logging, models base
├── features/        # Feature modules (auth, attendance, leaves, shifts, etc.)
│   └── {name}/
│       ├── routes.py
│       ├── schemas.py
│       ├── services/   # Feature-specific business logic
│       └── __init__.py
├── middleware/       # HTTP middleware (CORS, logging, CSRF, etc.)
├── models/           # SQLAlchemy ORM models
├── services/         # Shared database services (BaseService + feature services)
└── utils/            # JWT, storage, audit utilities
```

### Adding a New Feature
1. Create `app/features/{name}/` with `routes.py`, `schemas.py`, optionally `services/`
2. Use `APIRouter(prefix="/{name}", tags=["{name}"])` for routes
3. Register the router in `app/main.py` via `app.include_router(router, prefix="/api")`
4. Use `Depends(get_current_user)` for auth, `Depends(require_active)` for active-user gating
5. Import SQLAlchemy models from `app.models.{name}`

### Route Patterns (FastAPI)
```python
# Keep routes focused — delegate business logic to services
@router.get("/endpoint")
def handler(db: Session = Depends(get_db), user: dict = Depends(require_active)):
    ...
    return some_data

@router.post("/endpoint", status_code=201)
def create_handler(
    payload: SomeSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(require_active),
):
    ...
```

### Security Rules (Critical)
- **NEVER use f-strings or `.format()` for SQL queries.** Use parameterized queries:
  ```python
  # CORRECT: parameterized
  db.execute(text("SELECT * FROM t WHERE id = :id"), {"id": value})
  
  # WRONG: string interpolation
  db.execute(text(f"SELECT * FROM t WHERE id = {value}"))
  ```
- **No hardcoded secrets.** Use `settings` from `app.core.config` (reads from `.env` via pydantic-settings).
- Rate limiting is handled by `slowapi` — use `@limiter.exempt` sparingly.
- CSRF/Origin validation middleware is available but commented out in `main.py`.

### Logging
```python
import logging
logger = logging.getLogger(__name__)

# Use structured logging:
logger.info("message", extra={"extra_data": {...}})
```

### SQLAlchemy
- Use SQLAlchemy 2.0 style (select, update, delete via `sqlalchemy.sql`)
- Database sessions via FastAPI dependency: `db: Session = Depends(get_db)`
- Generic `BaseService[T]` in `app/services/database/base_service.py` provides CRUD operations
- Auto-migrations run in `app/core/database.py::_run_migrations()` on startup

### Configuration
- All settings defined in `app/core/config.py` (pydantic-settings `BaseSettings`)
- Override via environment variables or `.env` file
- Computed properties (URLs) derived from `KEYCLOAK_URL` + `REALM`

## Commands

| Command | Purpose |
|---------|---------|
| `pnpm dev` | Run backend in dev mode (hot reload) |
| `pnpm build` | Build all apps |
| `pnpm lint` | Lint all apps |
| `pnpm test` | Run tests |
| `pnpm format` | Format code (Prettier) |

## Best Practices

### Before a Task
- Read relevant existing files to understand conventions before writing new code.
- Mimic the style of surrounding code (imports, docstring format, error handling).
- Check if a reusable service/utility already exists before creating one.

### After Writing Code
- Always run `pnpm lint` and fix any issues.
- Run `pnpm test` if tests exist for the modified feature.
- Never leave commented-out code without explaining why.

### Code Quality
- All new modules and public functions **must** have docstrings matching existing conventions.
- Handle exceptions explicitly — don't use bare `except:`.
- Use type hints for all function signatures and class attributes.
- Respect the feature-based module boundary — don't create circular imports between features.

### General
- Prefer editing existing files over creating new ones.
- Never introduce new dependencies without asking.
- Never generate docs files (`*.md`) unless explicitly asked.
- Follow the existing logging pattern — log to `app.access` for request logs, module-level loggers for feature logs.
