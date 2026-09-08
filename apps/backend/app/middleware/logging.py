"""
HTTP request logging middleware.

This middleware logs every HTTP request that comes into the API.
It measures how long each request takes and logs the method, path,
status code, and duration.

What gets logged (as JSON in app.access logger):
- method: GET, POST, etc.
- path: /auth/login, /api/me, etc.
- status: HTTP status code (200, 401, etc.)
- duration_ms: how long the request took in milliseconds
- client_ip: IP address of the client
- correlation_id: the request's correlation ID

The log is written to:
- Console (stdout) for Docker logging
- logs/access.log as JSON (for log aggregation tools)
"""
import logging
import time

from fastapi import Request
from fastapi.responses import Response

logger = logging.getLogger("app.access")


def _get_security_ip(request: Request) -> str:
    """Return the client IP for security decisions (rate limiting, blocking).

    Uses only the TCP peer's address (request.client.host) which cannot be
    spoofed by the client. Headers like CF-Connecting-IP, X-Real-IP, or
    X-Forwarded-For are NOT trusted because they can be forged when the
    origin is directly accessible.

    Use this for: brute-force protection, IP blocklists, rate limiting.
    Use _get_client_ip() for: audit logging, analytics (where header spoofing
    is acceptable since it only affects logging).
    """
    return request.client.host if request.client else "-"


def _get_client_ip(request: Request) -> str:
    """Return the client IP for logging/analytics purposes.

    This function trusts client-supplied headers (CF-Connecting-IP, X-Real-IP,
    etc.) for visibility purposes. For SECURITY decisions (rate limiting,
    IP blocking), use _get_security_ip() instead.

    Priority:
    1. X-User-Public-IP — set by the frontend (browser) when the user is on the
       public internet. This is the real public IP and is what audit logs should
       show when the frontend is in a private network (e.g. Hetzner VM).
    2. CF-Connecting-IP — set by Cloudflare if traffic goes through CF.
    3. X-Real-IP — set by nginx (or any reverse proxy) when configured.
    4. X-Forwarded-For (leftmost) — first hop in the proxy chain.
    5. request.client.host — direct connection (no proxy). May be the
       frontend VM's private IP if the app is behind a private network.
    """
    user_public_ip = request.headers.get("x-user-public-ip", "").strip()
    if user_public_ip:
        return user_public_ip
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "-"


async def log_requests(request: Request, call_next) -> Response:
    """
    FastAPI middleware that logs HTTP requests with timing.
    
    How it works:
    1. Record the start time
    2. Process the request (call the next middleware/route handler)
    3. Calculate how long it took
    4. Log the request details
    5. Add timing and correlation ID to response headers
    """
    start_time = time.time()
    correlation_id = request.state.correlation_id

    # Process the request
    response = await call_next(request)

    # Calculate duration
    process_time = time.time() - start_time
    response.headers["X-Correlation-ID"] = correlation_id

    # Log the request details
    logger.info(
        f"{request.method} {request.url.path} {response.status_code} {process_time:.4f}s",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(process_time * 1000, 2),
                "client_ip": _get_client_ip(request),
            },
        },
    )

    return response
