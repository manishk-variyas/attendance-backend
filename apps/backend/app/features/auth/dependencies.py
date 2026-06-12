"""
Authentication dependencies for protecting routes.

This module provides FastAPI dependencies that can be used to protect
endpoints by requiring a valid session and optionally specific roles.

Main dependencies:
- `get_current_user` — validates session, returns user dict (sub, username, email, roles)
- `require_role(role)` — factory to create a dependency that checks for a specific role
- `require_admin` — pre-built dependency requiring the "admin" role

Usage:
    @app.get("/admin-only")
    def admin_endpoint(current_user: dict = Depends(get_current_user),
                       _=Depends(require_admin)):
        return "secret data"
"""
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session as DBSession
from app.features.auth.services.session import get_session
from app.core.database import get_db


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency that validates the user's session and returns user info.
    
    How it works:
    1. Get the session_id from the HTTP cookie
    2. Look up that session in the session store
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


def require_role(role: str):
    """
    Factory: returns a FastAPI dependency that checks the authenticated
    user has the given realm role.

    Must be used *after* `get_current_user` so that `current_user` is resolved
    first (FastAPI handles this automatically via the parameter chain).

    Usage:
        @router.post("/signup")
        async def signup(..., _: None = Depends(require_role("admin"))):
            ...
    """
    async def _role_checker(current_user: dict = Depends(get_current_user)) -> None:
        roles = current_user.get("roles", [])
        if role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires '{role}' role",
            )
    return _role_checker


# Convenience: pre-built admin guard
# Note: casing must match Keycloak realm role name exactly
require_admin = require_role("Admin")


async def require_active(
    db: DBSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> None:
    """
    Dependency: blocks deactivated employees from accessing any endpoint.

    Must be used after get_current_user. Looks up employee_master by email
    from the session and checks is_active is True. Returns 403 if deactivated.

    Usage:
        @router.get("/some-route")
        async def route(..., _: None = Depends(require_active)):
            ...
    """
    from app.models.employee_master import EmployeeMaster
    from app.services.database.base_service import BaseService

    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Session missing email")

    svc = BaseService[EmployeeMaster](db)
    emp = svc.fetch_one(EmployeeMaster, user_email=email)
    if not emp:
        raise HTTPException(status_code=403, detail="Employee record not found")
    if not emp.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated. Contact admin.")
