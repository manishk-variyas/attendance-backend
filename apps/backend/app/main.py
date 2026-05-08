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
from app.features.auth.routes import router as auth_router
from app.features.auth.dependencies import get_current_user
from app.features.redmine.routes import router as redmine_router


setup_logging()

root_logger = logging.getLogger(__name__)


app = FastAPI(
    title="Backend API with Keycloak Authentication", version="1.0", lifespan=lifespan
)

# Add rate limiting middleware to track and limit requests per IP
app.add_middleware(SlowAPIMiddleware)
app.state.limiter = limiter


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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add custom middleware for request logging and correlation ID tracking
# Order matters: correlation ID is set first, then logging uses it
app.middleware("http")(log_requests)
app.middleware("http")(set_correlation_id)


@app.get("/")
def root():
    """Root endpoint - returns basic API status info."""
    return {"message": "Backend API", "status": "running"}


@app.get("/health")
def health_check():
    """Health check endpoint - used by Docker/load balancers to check if API is alive."""
    return {"status": "ok"}


@app.get("/server-time")
def get_server_time():
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
from app.routes.recordings import router as recordings_router
app.include_router(recordings_router, prefix="/api", tags=["recordings"])

