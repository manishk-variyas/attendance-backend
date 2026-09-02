"""
PostgreSQL-backed session service — survives restarts, works across containers.
Original in-memory version is commented below for fallback.
"""
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from sqlalchemy.orm import Session as DBSession
from sqlalchemy import text

from app.core.config import settings
from app.models.session import Session

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  PUBLIC API — same interface, PostgreSQL backend
# ═══════════════════════════════════════════════════════════════

def _db() -> DBSession:
    """Get a raw DB session. Needed because this module is called
    before FastAPI dependency injection is available (login flow)."""
    from app.core.database import SessionLocal
    return SessionLocal()


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


async def create_session(user_data: dict, keycloak_refresh_token: str = None, expires_hours: int = None, is_active: bool = True, last_activity_at: datetime = None, realm: str | None = None) -> str:
    session_id = generate_session_id()
    expire_hours = expires_hours or settings.SESSION_EXPIRE_HOURS

    session_data = {
        "sub": user_data.get("sub"),
        "username": user_data.get("username"),
        "email": user_data.get("email"),
        "roles": user_data.get("roles", []),
        "is_active": is_active,
        "realm": realm,
    }
    if keycloak_refresh_token:
        session_data["kc_refresh_token"] = keycloak_refresh_token

    now = datetime.now(timezone.utc)
    db = _db()
    try:
        s = Session(
            id=session_id,
            user_sub=user_data.get("sub"),
            data=session_data,
            expires_at=now + timedelta(hours=expire_hours),
            last_activity_at=last_activity_at or now,
            is_active=True,
            display_id=str(uuid.uuid4()),
        )
        db.add(s)
        db.commit()
    finally:
        db.close()

    logger.info(f"Session created: {session_id[:8]}...")
    return session_id


async def get_session(session_id: str, touch_activity: bool = True) -> Optional[dict]:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s:
            return None
        # NEW: treat soft-deleted sessions as invalid
        if s.is_active is False:
            return None
        now = datetime.now(timezone.utc)
        if now > s.expires_at:
            # Soft-delete on expiry (was: hard-delete)
            s.is_active = False
            s.logged_out_at = now
            db.commit()
            logger.info(f"Session expired: {session_id[:8]}...")
            return None
            # ORIGINAL HARD-DELETE (commented, kept for easy revert):
            # db.delete(s)
            # db.commit()
            # logger.info(f"Session expired: {session_id[:8]}...")
            # return None

        # Idle timeout — sliding window based on last real activity.
        last = s.last_activity_at or s.created_at
        if now - last > timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES):
            # Soft-delete on idle expiry (was: hard-delete)
            s.is_active = False
            s.logged_out_at = now
            db.commit()
            logger.info(f"Session idle expired: {session_id[:8]}...")
            return None
            # ORIGINAL HARD-DELETE (commented, kept for easy revert):
            # db.delete(s)
            # db.commit()
            # logger.info(f"Session idle expired: {session_id[:8]}...")
            # return None

        # Throttled activity touch (avoid a DB write on every request).
        if touch_activity and (now - last) > timedelta(seconds=settings.SESSION_ACTIVITY_FLUSH_SECONDS):
            s.last_activity_at = now
            db.commit()

        return s.data
    finally:
        db.close()


async def get_session_email(session_id: str) -> Optional[str]:
    """Return the user's email from a session's data blob, even if the session
    is already soft-deleted. Used for audit logging on logout."""
    if not session_id:
        return None
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s or not isinstance(s.data, dict):
            return None
        return s.data.get("email") or s.data.get("username")
    finally:
        db.close()


async def get_session_last_activity(session_id: str) -> Optional[datetime]:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s:
            return None
        return s.last_activity_at
    finally:
        db.close()


async def delete_session(session_id: str) -> bool:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if s:
            # Soft-delete: keep row for history, mark inactive
            s.is_active = False
            s.logged_out_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Session soft-deleted: {session_id[:8]}...")
            return True
            # ORIGINAL HARD-DELETE (commented, kept for easy revert):
            # db.delete(s)
            # db.commit()
            # logger.info(f"Session deleted: {session_id[:8]}...")
            # return True
        return False
    finally:
        db.close()


async def delete_sessions_by_sub(sub: str) -> int:
    db = _db()
    try:
        # Soft-delete: keep rows for history, mark all inactive
        now = datetime.now(timezone.utc)
        rows = db.query(Session).filter(Session.user_sub == sub, Session.is_active == True).all()
        for s in rows:
            s.is_active = False
            s.logged_out_at = now
        db.commit()
        if rows:
            logger.info(f"Soft-deleted {len(rows)} sessions for sub: {sub}")
        return len(rows)
        # ORIGINAL HARD-DELETE (commented, kept for easy revert):
        # deleted = db.query(Session).filter(Session.user_sub == sub).delete()
        # db.commit()
        # if deleted > 0:
        #     logger.info(f"Deleted {deleted} sessions for sub: {sub}")
        # return deleted
    finally:
        db.close()


async def enforce_session_limit(sub: str, max_sessions: int = 2) -> int:
    db = _db()
    try:
        # Only consider active sessions when enforcing the limit
        sessions = db.query(Session).filter(
            Session.user_sub == sub,
            Session.is_active == True,
        ).order_by(Session.created_at.desc()).all()
        now = datetime.now(timezone.utc)
        soft_deleted = 0
        for s in sessions[max_sessions:]:
            s.is_active = False
            s.logged_out_at = now
            soft_deleted += 1
        db.commit()
        if soft_deleted > 0:
            logger.info(f"Enforced session limit: soft-deleted {soft_deleted} extra for {sub}")
        return soft_deleted
        # ORIGINAL HARD-DELETE (commented, kept for easy revert):
        # sessions = db.query(Session).filter(Session.user_sub == sub).order_by(Session.created_at.desc()).all()
        # deleted = 0
        # for s in sessions[max_sessions:]:
        #     db.delete(s)
        #     deleted += 1
        # db.commit()
        # if deleted > 0:
        #     logger.info(f"Enforced session limit: deleted {deleted} extra for {sub}")
        # return deleted
    finally:
        db.close()


async def refresh_session(session_id: str, keycloak_refresh_token: str) -> bool:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s:
            return False
        data = dict(s.data) if s.data else {}
        data["kc_refresh_token"] = keycloak_refresh_token
        s.data = data
        db.commit()
        return True
    finally:
        db.close()


async def extend_session(session_id: str, hours: int = None) -> bool:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s:
            return False
        expire_hours = hours or settings.SESSION_EXPIRE_HOURS
        s.expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
        db.commit()
        return True
    finally:
        db.close()


