"""
Authentication dependencies for protecting routes.

This module provides FastAPI dependencies that can be used to protect
endpoints by requiring a valid session.

The main dependency is `get_current_user` which:
1. Reads the session_id cookie from the request
2. Looks up the session in Redis
3. Returns the user data if session is valid
4. Raises 401 Unauthorized if session is missing or expired

Usage:
    @app.get("/api/me")
    def get_me(current_user: dict = Depends(get_current_user)):
        return current_user
"""
from fastapi import Request, HTTPException
from app.services.session import get_session


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency that validates the user's session and returns user info.
    
    How it works:
    1. Get the session_id from the HTTP cookie
    2. Look up that session in Redis
    3. If found, return the user data (sub, username, email, roles)
    4. If not found or missing, raise 401 Unauthorized
    
    This is used with FastAPI's Depends() to protect routes.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session_data = await get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return {
        "sub": session_data.get("sub"),
        "username": session_data.get("username"),
        "email": session_data.get("email"),
        "roles": session_data.get("roles", []),
    }
