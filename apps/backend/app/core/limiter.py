"""
Rate limiting configuration using slowapi.

This module sets up rate limiting to prevent abuse and brute force attacks.
It uses slowapi (built on limits library) to track requests per IP address.

What it does:
- Limits how many requests an IP can make in a time period
- Different limits can be applied to different endpoints
- Uses the client's IP address as the rate limit key

Configuration:
- RATE_LIMIT_PER_SECOND from settings controls the default rate
- LOGIN_RATE_LIMIT is stricter (half the default rate) for login attempts
"""
from slowapi import Limiter

from app.core.config import settings

# Default rate limit for all un-decorated endpoints
DEFAULT_RATE_LIMIT = f"{settings.RATE_LIMIT_PER_SECOND}/second"


def _client_key(request) -> str:
    """Return the client IP for rate limiting.

    Uses the TCP peer's address (request.client.host) which cannot be
    spoofed by the client. Headers like X-Real-IP or CF-Connecting-IP
    are not trusted because they can be forged when the origin is
    directly accessible.
    """
    return request.client.host if request.client else "unknown"


# Create a limiter with a global default limit
limiter = Limiter(key_func=_client_key, default_limits=[DEFAULT_RATE_LIMIT])

LOGIN_RATE_LIMIT = f"{max(1, settings.RATE_LIMIT_PER_SECOND // 2)}/minute"
