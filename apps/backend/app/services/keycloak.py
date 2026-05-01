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


async def get_admin_token() -> str:
    """Get admin access token using client credentials."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.KEYCLOAK_URL}/realms/{settings.REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to get admin token")
    return response.json()["access_token"]


async def create_keycloak_user(username: str, email: str) -> str:
    """Create a user in Keycloak. Returns user ID."""
    admin_token = await get_admin_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users",
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )

    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="Username already exists")
    if response.status_code == 400 and "email" in response.text:
        raise HTTPException(status_code=409, detail="Email already exists")

    if response.status_code != 201:
        raise HTTPException(status_code=500, detail="Failed to create user")

    user_id = response.headers["Location"].split("/")[-1]
    return user_id


async def set_keycloak_password(user_id: str, password: str) -> bool:
    """Set password for a user in Keycloak."""
    admin_token = await get_admin_token()

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users/{user_id}/reset-password",
            json={
                "type": "password",
                "value": password,
                "temporary": False,
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )

    return response.status_code == 204
