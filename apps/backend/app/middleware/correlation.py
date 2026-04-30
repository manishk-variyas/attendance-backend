"""
Correlation ID middleware for request tracing.

This middleware adds a unique correlation ID to every HTTP request.
The correlation ID is used to trace a request across the entire system
(logs, external API calls, etc.).

How it works:
1. Check if the request already has a correlation ID (from a header or cookie)
2. If not, generate a new one (format: "req-" + 12 hex chars)
3. Store it in request.state so other parts of the app can access it
4. Add it to the response headers so the client can see it too

This helps with debugging: you can search all logs by correlation ID
to see the full story of what happened during a request.
"""
import uuid

from fastapi import Request
from fastapi.responses import Response


# Header name for correlation ID (can be sent by the client or upstream proxy)
CORRELATION_HEADER = "X-Correlation-ID"
# Cookie name (alternative way to pass correlation ID)
CORRELATION_COOKIE = "correlation_id"


def get_or_create_correlation_id(request: Request) -> str:
    """
    Get existing correlation ID from request, or create a new one.
    
    Check order:
    1. X-Correlation-ID header (from client or upstream)
    2. correlation_id cookie
    3. Generate a new UUID-based ID
    """
    # Check header first
    corr_id = request.headers.get(CORRELATION_HEADER)
    if corr_id:
        return corr_id

    # Check cookie next
    corr_id = request.cookies.get(CORRELATION_COOKIE)
    if corr_id:
        return corr_id

    # Generate a new short unique ID
    return f"req-{uuid.uuid4().hex[:12]}"


async def set_correlation_id(request: Request, call_next) -> Response:
    """
    FastAPI middleware that adds correlation ID to every request.
    
    This runs for every request:
    1. Get or create a correlation ID
    2. Store it in request.state (available to route handlers)
    3. Process the request
    4. Add the correlation ID to the response headers
    """
    correlation_id = get_or_create_correlation_id(request)
    request.state.correlation_id = correlation_id

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id

    return response
