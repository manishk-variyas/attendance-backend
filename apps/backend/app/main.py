"""
Main entry point for the FastAPI backend application.

This module:
- Creates the FastAPI app instance
- Registers all middleware (CORS, rate limiting, logging, correlation ID)
- Defines root endpoints: /, /health, /api/me
- Includes authentication routes from auth.router
- Sets up exception handlers for rate limiting

The app acts as a Backend-for-Frontend (BFF) that sits between
the frontend and Keycloak, using server-side sessions stored in Redis.
"""
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.lifespan import lifespan
from app.core.logging import setup_logging
from app.core.limiter import limiter
from app.middleware.correlation import set_correlation_id
from app.middleware.logging import log_requests
from app.middleware.active_employee import active_employee_middleware
from app.middleware.csrf import origin_validation_middleware
from app.features.auth.routes import router as auth_router
from app.features.auth.dependencies import get_current_user, require_active
from app.features.redmine.routes import router as redmine_router


setup_logging()

root_logger = logging.getLogger(__name__)


app = FastAPI(
    title="Backend API with Keycloak Authentication", version="1.0", lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,
)

# Add rate limiting middleware to track and limit requests per IP
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Handle requests that exceed the rate limit - return 429 Too Many Requests."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


# Configure CORS to allow frontend apps to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# Add custom middleware for request logging and correlation ID tracking
# Order matters: correlation ID is set first, then logging uses it
app.middleware("http")(log_requests)
app.middleware("http")(set_correlation_id)

# Active-employee guard — blocks deactivated accounts globally (before any route)
app.middleware("http")(active_employee_middleware)

# CSRF Protection: Validate Origin/Referer headers for state-changing requests
# app.middleware("http")(origin_validation_middleware)


@app.get("/")
@limiter.exempt
def root():
    return {"message": "Backend API", "status": "running"}


@app.get("/health")
@limiter.exempt
def health_check():
    return {"status": "ok"}


@app.get("/server-time")
def get_server_time(
    _: None = Depends(require_active),
):
    """Returns the current server time in UTC."""
    now = datetime.now(timezone.utc)
    return {
        "server_time": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "timezone": "UTC"
    }


@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """Return current logged-in user's info - requires valid session cookie."""
    return current_user


# Register authentication routes (login, logout, refresh, backchannel-logout)
app.include_router(auth_router)

# Register Redmine integration routes
app.include_router(redmine_router, prefix="/api", tags=["redmine"])

# Register Recording routes
from app.features.recordings.routes import router as recordings_router
app.include_router(recordings_router, prefix="/api", tags=["recordings"])

# Register Leave Management routes
from app.features.leaves.routes import router as leaves_router
app.include_router(leaves_router, prefix="/api", tags=["leaves"])

# Register Admin Leave Balance routes
from app.features.leaves.admin_routes import router as leave_balance_router
app.include_router(leave_balance_router, prefix="/api", tags=["admin-leave-balances"])

# Register Country routes
from app.features.countries import router as countries_router
app.include_router(countries_router, prefix="/api", tags=["countries"])

# Register Location Tracking routes
from app.features.location.routes import router as location_router
app.include_router(location_router, prefix="/api/location", tags=["location"])

# Register Admin Office Location routes
from app.features.location.admin_routes import router as office_location_router
app.include_router(office_location_router, prefix="/api", tags=["admin-office-locations"])

# Register Shift Management routes
from app.features.shifts.routes import router as shifts_router
app.include_router(shifts_router, prefix="/api", tags=["shifts"])

# Register Company Settings routes
from app.features.settings.routes import router as settings_router
app.include_router(settings_router, prefix="/api", tags=["settings"])

# Register Employee Management routes
from app.features.employees.routes import router as employees_router
app.include_router(employees_router, prefix="/api", tags=["employees"])

# Register Attendance routes
from app.features.attendance.routes import router as attendance_router
app.include_router(attendance_router, prefix="/api", tags=["attendance"])

# Register Admin Attendance routes
from app.features.attendance.admin_routes import router as admin_attendance_router
app.include_router(admin_attendance_router, prefix="/api", tags=["admin-attendance"])

