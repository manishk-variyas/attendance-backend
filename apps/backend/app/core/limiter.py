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

# Create a limiter that uses the client's IP address as the key
# This means each IP gets its own rate limit bucket
limiter = Limiter(key_func=get_remote_address)

# Stricter rate limit for login endpoint (prevents brute force attacks)
# Set to half the default rate, minimum 1 per minute
LOGIN_RATE_LIMIT = f"{max(1, settings.RATE_LIMIT_PER_SECOND // 2)}/minute"

# Default rate limit for other endpoints
DEFAULT_RATE_LIMIT = f"{settings.RATE_LIMIT_PER_SECOND}/second"
