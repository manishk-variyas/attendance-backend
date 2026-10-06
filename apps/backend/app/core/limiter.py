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
- LOGIN_RATE_LIMIT is a coarse per-IP flood guard for login (kept generous;
  see note below)
"""
from slowapi import Limiter

from app.core.client_ip import security_client_ip
from app.core.config import settings

# Default rate limit for all un-decorated endpoints
DEFAULT_RATE_LIMIT = f"{settings.RATE_LIMIT_PER_SECOND}/second"


def _client_key(request) -> str:
    """Return the real client IP for rate limiting.

    The API sits behind nginx (no published port), so we trust the proxy-set
    ``X-Real-IP`` / ``X-Forwarded-For`` headers. Keying on the raw TCP peer
    would bucket every user under the nginx container IP — one shared limit
    for the whole app. See ``app.core.client_ip``.
    """
    return security_client_ip(request)


# Create a limiter with a global default limit
limiter = Limiter(key_func=_client_key, default_limits=[DEFAULT_RATE_LIMIT])

# Coarse per-source-IP flood guard for login. In this deployment every request
# can arrive through one shared frontend/NAT IP, so a tight per-IP login limit
# would lock out all users at once. Keep it generous as a burst guard; precise
# brute-force protection is per-account in ``app.utils.login_security``
# (per-(account, IP) + per-account), which never keys on the IP alone.
LOGIN_RATE_LIMIT = f"{max(30, settings.RATE_LIMIT_PER_SECOND * 6)}/minute"
