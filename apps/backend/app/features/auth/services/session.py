"""
PostgreSQL-backed session service — survives restarts, works across containers.
Original in-memory version is commented below for fallback.
"""
import json
import logging
import secrets
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


async def create_session(user_data: dict, keycloak_refresh_token: str = None, expires_hours: int = None) -> str:
    session_id = generate_session_id()
    expire_hours = expires_hours or settings.SESSION_EXPIRE_HOURS

    session_data = {
        "sub": user_data.get("sub"),
        "username": user_data.get("username"),
        "email": user_data.get("email"),
        "roles": user_data.get("roles", []),
    }
    if keycloak_refresh_token:
        session_data["kc_refresh_token"] = keycloak_refresh_token

    db = _db()
    try:
        s = Session(
            id=session_id,
            user_sub=user_data.get("sub"),
            data=session_data,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=expire_hours),
        )
        db.add(s)
        db.commit()
    finally:
        db.close()

    logger.info(f"Session created: {session_id[:8]}...")
    return session_id


async def get_session(session_id: str) -> Optional[dict]:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if not s:
            return None
        if datetime.now(timezone.utc) > s.expires_at:
            db.delete(s)
            db.commit()
            logger.info(f"Session expired: {session_id[:8]}...")
            return None
        return s.data
    finally:
        db.close()


async def delete_session(session_id: str) -> bool:
    db = _db()
    try:
        s = db.query(Session).filter(Session.id == session_id).first()
        if s:
            db.delete(s)
            db.commit()
            logger.info(f"Session deleted: {session_id[:8]}...")
            return True
        return False
    finally:
        db.close()


async def delete_sessions_by_sub(sub: str) -> int:
    db = _db()
    try:
        deleted = db.query(Session).filter(Session.user_sub == sub).delete()
        db.commit()
        if deleted > 0:
            logger.info(f"Deleted {deleted} sessions for sub: {sub}")
        return deleted
    finally:
        db.close()


async def enforce_session_limit(sub: str, max_sessions: int = 2) -> int:
    db = _db()
    try:
        sessions = db.query(Session).filter(Session.user_sub == sub).order_by(Session.created_at.desc()).all()
        deleted = 0
        for s in sessions[max_sessions:]:
            db.delete(s)
            deleted += 1
        db.commit()
        if deleted > 0:
            logger.info(f"Enforced session limit: deleted {deleted} extra for {sub}")
        return deleted
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


# ═══════════════════════════════════════════════════════════════
#  OLD — IN-MEMORY SESSION STORE (kept for fallback)
# ═══════════════════════════════════════════════════════════════
#
# _session_store: Dict[str, dict] = {}
#
# async def create_session(user_data: dict, ...):
#     session_id = generate_session_id()
#     _session_store[session_id] = {"data": session_data, "expires": datetime.now() + timedelta(hours=expire_hours)}
#     return session_id
#
# async def get_session(session_id: str):
#     session = _session_store.get(session_id)
#     if not session or datetime.now() > session["expires"]:
#         ...
#
# async def delete_session(session_id: str):
#     ...
#
# async def delete_sessions_by_sub(sub: str):
#     ...
#
# async def enforce_session_limit(sub: str, max_sessions: int = 2):
#     ...
