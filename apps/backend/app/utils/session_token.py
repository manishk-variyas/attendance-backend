"""Resolve the session id for a request from a per-tab header or the cookie.

Browsers share cookies across every tab, so a cookie-only session means one
user per browser and "logout logs out all tabs". Preferring an
``Authorization: Bearer <token>`` (or ``X-Session-Id``) header lets the
frontend keep a **per-tab** token in ``sessionStorage`` while still supporting
non-SPA / mobile clients that use the cookie.
"""
from typing import Optional

from fastapi import Request

SESSION_HEADER = "X-Session-Id"


def get_request_session_id(request: Request) -> Optional[str]:
    """Session id from the Authorization header first, else the cookie."""
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    header_token = (request.headers.get(SESSION_HEADER) or "").strip()
    if header_token:
        return header_token
    return request.cookies.get("session_id")
