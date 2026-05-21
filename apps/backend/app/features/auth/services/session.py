"""
In-memory session service - manages user sessions stored in the server's memory.

This module handles all session-related operations using a local dictionary:
- Creating sessions (after login)
- Getting session data
- Deleting sessions (logout)
- Refreshing sessions
- Extending session expiry

Note: In-memory sessions are lost when the server restarts.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict

# Redis imports commented out as requested
# import redis.asyncio as redis
from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory session store
# Key: session_id, Value: {data: dict, expires: datetime}

# ===========================================================================================
# Todo(Pending) - Should be persistent (Fix!!!!!)
# ===========================================================================================
_session_store: Dict[str, dict] = {}

# ===========================================================================================
# ===========================================================================================
# ===========================================================================================


async def get_redis():
    """Redis is disabled - returning None as per migration request."""
    return None


async def close_redis():
    """Redis is disabled."""
    pass


def generate_session_id() -> str:
    """Generate a secure random session ID."""
    return secrets.token_urlsafe(32)


async def create_session(
    user_data: dict, keycloak_refresh_token: str = None, expires_hours: int = None
) -> str:
    """Create a new session in server memory."""
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

    # Store in memory with expiry
    _session_store[session_id] = {
        "data": session_data,
        "expires": datetime.now() + timedelta(hours=expire_hours)
    }
    
    logger.info(f"Session created in-memory: {session_id[:8]}...")
    return session_id


async def get_session(session_id: str) -> Optional[dict]:
    """Get session data from memory, checking for expiry."""
    session = _session_store.get(session_id)
    if not session:
        return None

    # Check if session has expired
    if datetime.now() > session["expires"]:
        logger.info(f"Session expired in-memory: {session_id[:8]}...")
        del _session_store[session_id]
        return None

    return session["data"]


async def delete_session(session_id: str) -> bool:
    """Delete a session from memory (logout)."""
    if session_id in _session_store:
        del _session_store[session_id]
        logger.info(f"Session deleted from memory: {session_id[:8]}...")
        return True
    return False


async def delete_sessions_by_sub(sub: str) -> int:
    """Delete all sessions belonging to a specific user (sub)."""
    deleted = 0
    keys_to_delete = [
        sid for sid, info in _session_store.items() 
        if info.get("data", {}).get("sub") == sub
    ]
            
    for sid in keys_to_delete:
        del _session_store[sid]
        deleted += 1
        
    if deleted > 0:
        logger.info(f"Deleted {deleted} in-memory sessions for user sub: {sub}")
        
    return deleted


async def refresh_session(session_id: str, keycloak_refresh_token: str) -> bool:
    """Update the refresh token for an existing in-memory session."""
    session = _session_store.get(session_id)
    if not session:
        return False

    session["data"]["kc_refresh_token"] = keycloak_refresh_token
    return True


async def extend_session(session_id: str, hours: int = None) -> bool:
    """Extend the expiry time of an existing in-memory session."""
    session = _session_store.get(session_id)
    if not session:
        return False

    expire_hours = hours or settings.SESSION_EXPIRE_HOURS
    session["expires"] = datetime.now() + timedelta(hours=expire_hours)
    return True
