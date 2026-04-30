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
    response.headers["X-Process-Time"] = f"{process_time:.4f}"
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
                "client_ip": request.client.host if request.client else "-",
            },
        },
    )

    return response
