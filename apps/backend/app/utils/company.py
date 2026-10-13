"""Company identity resolution — single source for brand and portal URL.

- Brand (company name / trademark): company settings row first,
  `COMPANY_NAME` env second, generic default last.
- Portal URL (app links in emails): env `PORTAL_URL` first, `FRONTEND_URL`
  second, localhost last. Deliberately NOT the `website_url` settings field,
  which is the marketing site (different host) — see docs.
"""

from typing import Optional

from app.core.cache import get_company_settings
from app.core.config import settings
from app.core.database import SessionLocal

_LOCALHOST_PORTAL = "http://localhost:5173"


def _company_row():
    """Fetch the detached company settings row, or None on any failure."""
    try:
        db = SessionLocal()
        try:
            return get_company_settings(db)
        finally:
            db.close()
    except Exception:
        return None


def resolve_brand_name(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    row = _company_row()
    if row is not None and getattr(row, "company_name", None):
        name = (row.company_name or "").strip()
        if name:
            return name
    if settings.BRAND_NAME and settings.BRAND_NAME.strip():
        return settings.BRAND_NAME.strip()
    if settings.COMPANY_NAME and settings.COMPANY_NAME.strip():
        return settings.COMPANY_NAME.strip()
    return settings.DEFAULT_BRAND_NAME or "Attendance Portal"


def resolve_trademark(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    row = _company_row()
    if row is not None and getattr(row, "trademark", None):
        tm = (row.trademark or "").strip()
        if tm:
            return tm
    return resolve_brand_name()


def resolve_portal_url(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip().rstrip("/")
    if settings.PORTAL_URL and settings.PORTAL_URL.strip():
        return settings.PORTAL_URL.strip().rstrip("/")
    if settings.FRONTEND_URL and settings.FRONTEND_URL.strip():
        return settings.FRONTEND_URL.strip().rstrip("/")
    return _LOCALHOST_PORTAL
