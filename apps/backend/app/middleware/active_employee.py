"""
Middleware that blocks deactivated employees from accessing any protected endpoint.

Runs globally on every request, before route handlers.
Only public endpoints (/, /health, /server-time, /api/me) bypass this check.
"""
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.features.auth.services.session import get_session
from app.core.database import SessionLocal
from app.models.employee_master import EmployeeMaster


PUBLIC_PATHS = {"/", "/health", "/server-time", "/api/me"}


async def active_employee_middleware(request: Request, call_next) -> Response:
    path = request.url.path

    if path in PUBLIC_PATHS:
        return await call_next(request)

    session_id = request.cookies.get("session_id")
    if not session_id:
        return await call_next(request)

    session_data = await get_session(session_id)
    if not session_data:
        return await call_next(request)

    email = session_data.get("email")
    if not email:
        return await call_next(request)

    roles = session_data.get("roles", [])
    if "Admin" in roles:
        return await call_next(request)

    db = SessionLocal()
    try:
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
        if emp and not emp.is_active:
            return JSONResponse(
                status_code=403,
                content={"detail": "Account is deactivated. Contact admin."},
            )
    finally:
        db.close()

    return await call_next(request)
