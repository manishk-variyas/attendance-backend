"""
Redis session service - manages user sessions stored in Redis.

This module handles all session-related operations using Redis as the backend:
- Creating sessions (after login)
- Getting session data
- Deleting sessions (logout)
- Refreshing sessions
- Extending session expiry

Session data structure in Redis:
    Key: "session:{session_id}"
    Value: JSON string with:
        - sub: Keycloak user ID
        - username: user's username
        - email: user's email
        - roles: list of user roles
        - kc_refresh_token: Keycloak refresh token (for refreshing sessions)

Sessions expire automatically after SESSION_EXPIRE_HOURS.
"""
import json
import logging
import secrets
from typing import Optional

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global Redis client (initialized on first use)
redis_client: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """
    Get or create the Redis client.
    
    Uses a singleton pattern - creates the client once and reuses it.
    The client is created with decode_responses=True so we get strings,
    not bytes, from Redis.
    """
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client


async def close_redis():
    """
    Close the Redis connection.
    
    Called during app shutdown to clean up resources.
    """
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


def generate_session_id() -> str:
    """
    Generate a secure random session ID.
    
    Uses secrets.token_urlsafe() which is cryptographically secure
    and URL-safe (no special characters that need encoding).
    
    Returns:
        str: A 43-character URL-safe random string
    """
    return secrets.token_urlsafe(32)


def _session_key(session_id: str) -> str:
    """
    Convert a session ID to a Redis key.
    
    Adds the "session:" prefix to avoid key collisions with other data.
    """
    return f"session:{session_id}"


async def create_session(
    user_data: dict, keycloak_refresh_token: str = None, expires_hours: int = None
) -> str:
    """
    Create a new session in Redis.
    
    How it works:
    1. Generate a new unique session ID
    2. Build the session data with user info and refresh token
    3. Store as JSON in Redis with an expiry time
    
    Args:
        user_data: Dict with sub, username, email, roles
        keycloak_refresh_token: The Keycloak refresh token (for later refresh)
        expires_hours: Override the default session expiry
    
    Returns:
        str: The new session ID (to be set as a cookie)
    """
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

    r = await get_redis()
    await r.set(
        _session_key(session_id),
        json.dumps(session_data),
        ex=expire_hours * 3600,  # Convert hours to seconds for Redis
    )
    return session_id


async def get_session(session_id: str) -> Optional[dict]:
    """
    Get session data from Redis.
    
    Args:
        session_id: The session ID from the cookie
        
    Returns:
        dict or None: The session data if found, None if expired/not found
    """
    r = await get_redis()
    data = await r.get(_session_key(session_id))
    if data:
        return json.loads(data)
    return None


async def delete_session(session_id: str) -> bool:
    """
    Delete a session from Redis (logout).
    
    Args:
        session_id: The session ID to delete
        
    Returns:
        bool: True if a session was deleted, False if it didn't exist
    """
    r = await get_redis()
    result = await r.delete(_session_key(session_id))
    return result > 0


async def refresh_session(session_id: str, keycloak_refresh_token: str) -> bool:
    """
    Update the refresh token for an existing session.
    
    Used internally when refreshing a session - updates just the refresh token
    without changing other session data.
    
    Args:
        session_id: The session to update
        keycloak_refresh_token: The new refresh token
        
    Returns:
        bool: True if session was found and updated, False otherwise
    """
    r = await get_redis()
    data = await r.get(_session_key(session_id))
    if not data:
        return False

    session_data = json.loads(data)
    session_data["kc_refresh_token"] = keycloak_refresh_token
    await r.set(_session_key(session_id), json.dumps(session_data))
    return True


async def extend_session(session_id: str, hours: int = None) -> bool:
    """
    Extend the expiry time of an existing session.
    
    Useful for "sliding sessions" that expire after inactivity.
    
    Args:
        session_id: The session to extend
        hours: New expiry time in hours (uses default if not specified)
        
    Returns:
        bool: True if session was found and extended, False otherwise
    """
    r = await get_redis()
    expire_hours = hours or settings.SESSION_EXPIRE_HOURS
    return await r.expire(_session_key(session_id), expire_hours * 3600)
