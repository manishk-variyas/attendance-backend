from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.features.auth.services.session import get_session


PUBLIC_PATHS = {"/", "/health", "/server-time", "/api/me"}


async def active_employee_middleware(request: Request, call_next) -> Response:
    path = request.url.path

    if path in PUBLIC_PATHS:
        return await call_next(request)

    session_id = request.cookies.get("session_id")
    if not session_id:
        return await call_next(request)

    # Keepalive refresh must NOT reset the idle clock — only real activity does.
    session_data = await get_session(session_id, touch_activity=(path != "/auth/refresh"))
    if not session_data:
        return await call_next(request)

    request.state.session_data = session_data

    roles = session_data.get("roles", [])
    if "Admin" in roles:
        return await call_next(request)

    is_active = session_data.get("is_active", True)
    if not is_active:
        return JSONResponse(
            status_code=403,
            content={"detail": "Account is deactivated. Contact admin."},
        )

    return await call_next(request)
