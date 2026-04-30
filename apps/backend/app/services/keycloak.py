"""
Keycloak service - handles communication with Keycloak identity provider.

This module provides functions to interact with Keycloak's API:
- Refresh tokens (get new access tokens using a refresh token)
- Revoke tokens (logout from Keycloak side)
- Get JWKS (JSON Web Key Set for verifying JWT signatures)

Keycloak is the identity provider that handles user authentication.
This backend acts as a client to Keycloak, using the client credentials
flow (client_id + client_secret) plus user credentials when needed.
"""
import json
import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


async def refresh_keycloak_token(refresh_token: str) -> dict:
    """
    Refresh an expired access token using a refresh token.
    
    How it works:
    1. Send the refresh token to Keycloak's token endpoint
    2. Keycloak validates it and returns new tokens
    3. Returns the new tokens (access_token, refresh_token, etc.)
    
    Returns:
        dict: New tokens from Keycloak
        
    Raises:
        HTTPException: If refresh fails (invalid/expired refresh token)
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to refresh Keycloak token")

    return response.json()


async def revoke_keycloak_token(refresh_token: str) -> bool:
    """
    Revoke a refresh token in Keycloak (logout).
    
    This tells Keycloak that the refresh token is no longer valid.
    After this, the user can't refresh their session anymore.
    
    Returns:
        bool: True if revocation was successful, False otherwise
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.LOGOUT_URL,
            data={
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code in [200, 204]:
        logger.info("Keycloak token revoked successfully")
        return True
    else:
        logger.warning(
            f"Failed to revoke Keycloak token: {response.status_code} - {response.text}"
        )
        return False


async def get_jwks() -> dict:
    """
    Get Keycloak's JSON Web Key Set (JWKS).
    
    The JWKS contains the public keys that Keycloak uses to sign JWTs.
    These keys are needed to verify that a JWT was actually issued by Keycloak.
    
    How it works:
    1. Check Redis cache first (keys don't change often)
    2. If not cached, fetch from Keycloak's JWKS endpoint
    3. Cache the result in Redis for 1 hour
    
    Returns:
        dict: The JWKS containing public keys
    """
    from app.services.session import get_redis

    r = await get_redis()
    cached = await r.get("jwks:keys")
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient() as client:
        response = await client.get(settings.JWKS_URL)
        response.raise_for_status()
        jwks = response.json()

    # Cache JWKS for 1 hour (they don't change often)
    await r.setex("jwks:keys", 3600, json.dumps(jwks))
    return jwks
