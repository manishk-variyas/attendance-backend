"""
Admin-only sessions API.

Read-only view of user session activity (active + historical).
NEVER exposes the actual session id (bearer credential) — only an opaque `display_id`.

Status rules:
  - logged_out_at IS NOT NULL                  -> "ended"
  - logged_out_at IS NULL AND expires_at < now -> "expired"
  - logged_out_at IS NULL AND last_activity_at
      < now - idle_timeout                     -> "idle_expired"
  - else                                       -> "active"
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import desc

from app.core.database import get_db
from app.core.config import settings
from app.core.limiter import limiter
from app.features.auth.dependencies import get_current_user
from app.models.session import Session

router = APIRouter(prefix="/admin/sessions", tags=["admin-sessions"])


def _compute_status(s: Session, now: datetime) -> str:
    if s.logged_out_at is not None:
        return "ended"
    if s.expires_at and s.expires_at < now:
        return "expired"
    last = s.last_activity_at or s.created_at
    if last and now - last > timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES):
        return "idle_expired"
    return "active"


def _serialize_session(s: Session, now: datetime) -> dict:
    status = _compute_status(s, now)
    if status == "active":
        duration = now - s.created_at
    else:
        end = s.logged_out_at or s.last_activity_at or s.created_at
        duration = end - s.created_at
    email = None
    if isinstance(s.data, dict):
        email = s.data.get("email")
    return {
        "displayId": s.display_id,
        "email": email,
        "loginTime": s.created_at.isoformat() if s.created_at else None,
        "lastActivityAt": s.last_activity_at.isoformat() if s.last_activity_at else None,
        "logoutTime": s.logged_out_at.isoformat() if s.logged_out_at else None,
        "durationSeconds": int(duration.total_seconds()) if duration else 0,
        "status": status,
    }


@router.get("")
@limiter.limit("10/minute")
async def list_sessions(
    request: Request,
    current_user: dict = Depends(get_current_user),
    search: Optional[str] = Query(None, max_length=100, description="Case-insensitive substring on email"),
    status_filter: Optional[str] = Query(None, alias="status", description="active, ended, expired, idle_expired"),
    since: Optional[str] = Query(None, max_length=10, description="Filter sessions with loginTime >= this date (YYYY-MM-DD)"),
    until: Optional[str] = Query(None, max_length=10, description="Filter sessions with loginTime <= this date (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(10, ge=1, le=100),
    db: DBSession = Depends(get_db),
):
    """List user sessions (active + historical). Admin only. Never returns session_id."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    now = datetime.now(timezone.utc)

    query = db.query(Session).filter(Session.display_id.isnot(None))
    if since:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since format. Use YYYY-MM-DD.")
        query = query.filter(Session.created_at >= since_dt)
    if until:
        try:
            until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid until format. Use YYYY-MM-DD.")
        query = query.filter(Session.created_at < until_dt)

    all_rows = query.order_by(desc(Session.created_at)).all()
    serialized = [_serialize_session(s, now) for s in all_rows]

    if search:
        needle = search.casefold()
        serialized = [r for r in serialized if needle in (r.get("email") or "").casefold()]

    if status_filter:
        wanted = status_filter.strip().lower()
        serialized = [r for r in serialized if r.get("status") == wanted]

    total = len(serialized)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    offset = (page - 1) * page_size
    items = serialized[offset:offset + page_size]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/{display_id}")
@limiter.limit("10/minute")
async def get_session_detail(
    request: Request,
    display_id: str,
    current_user: dict = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Get one session record by display_id. Admin only. Never returns session_id."""
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    s = db.query(Session).filter(Session.display_id == display_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _serialize_session(s, datetime.now(timezone.utc))
