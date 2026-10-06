# Conventions

- **Feature layout**: `app/features/<feature>/{routes,schemas,service}.py`.
  Routes stay thin; logic in the service.
- **DB access**: SQLAlchemy `Session` via `Depends(get_db)`; raw SQL with
  `text(...)` and bound params (never string-format user input).
- **Schemas**: Pydantic response models in `schemas.py`; keep field names
  snake_case.
- **Dates**: `YYYY-MM-DD`; day boundaries in **IST**.
- **Comments**: do not add unless asked. Docstrings only where they add value.
- **Imports**: stdlib, third-party, then app modules. No unused imports.
- **Errors**: raise `HTTPException` with a short `detail`.
- **Sorting**: make SQL `ORDER BY` deterministic (add a tiebreak column).
- **Queries**: avoid N+1 — batch with `ANY(:ids)`.
- **Tests/verification**: prefer independent DB recomputation over asserting the
  same code path. See `runbook.md`.

## Do not
- Do not trust client-supplied IP headers for security (only proxy-set ones).
- Do not assume weekdays are working days — use shifts (see `analytics.md`).
- Do not `git commit` unless explicitly asked.
