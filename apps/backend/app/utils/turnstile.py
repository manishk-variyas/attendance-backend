"""Cloudflare Turnstile server-side verification.

When `TURNSTILE_SECRET_KEY` is set in the environment, /auth/login and
/auth/forgot-password require a `turnstileToken` field on the request body.
The token is verified against Cloudflare's siteverify API.

If the secret key is empty (default), verification is a no-op so local
development and existing flows keep working.
"""
import httpx
from fastapi import HTTPException, status

from app.core.config import settings


_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str | None) -> bool:
    """Verify a Cloudflare Turnstile token. Raises 400/403 on failure.

    If `TURNSTILE_SECRET_KEY` is empty, this is a no-op (returns True).
    If `TURNSTILE_ENABLED` is False, this is a no-op (returns True) — useful
    for staged rollouts.

    Note: error messages are intentionally vague so we don't give attackers
    a hint about whether they need to obtain a Turnstile token. A missing
    token and an invalid token both yield the same generic 400.
    """
    if not settings.TURNSTILE_SECRET_KEY or not settings.TURNSTILE_ENABLED:
        return True

    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request.",
        )

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                _VERIFY_URL,
                data={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
                timeout=10.0,
            )
            result = response.json()
        except Exception as e:
            # If we can't reach Cloudflare (e.g. CI, dev without internet), fail
            # open so the user isn't permanently locked out. Production should
            # set TURNSTILE_ENABLED=False only if the verification endpoint is
            # guaranteed reachable.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invalid request.",
            )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request.",
        )
    return True
