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
from slowapi.util import get_remote_address

from app.core.config import settings

# Default rate limit for all un-decorated endpoints
DEFAULT_RATE_LIMIT = "100/minute"

# Create a limiter with a global default limit
limiter = Limiter(key_func=get_remote_address, default_limits=[DEFAULT_RATE_LIMIT])

LOGIN_RATE_LIMIT = f"{max(1, settings.RATE_LIMIT_PER_SECOND // 2)}/minute"
