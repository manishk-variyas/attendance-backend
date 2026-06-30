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
    async with httpx.AsyncClient() as client:
        response = await client.get(settings.JWKS_URL)
        response.raise_for_status()
        return response.json()


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


async def create_keycloak_user(username: str, email: str, timezone: str = "UTC") -> str:
    """Create a user in Keycloak. Returns user ID.
    
    Stores timezone as a user attribute so it gets embedded in the JWT token
    (requires a 'User Attribute' mapper named 'timezone' on the Keycloak client).
    """
    admin_token = await get_admin_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users",
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
                # Keycloak stores attributes as lists
                "attributes": {
                    "timezone": [timezone],
                },
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


async def get_realm_roles() -> list:
    """Fetch all realm roles from Keycloak."""
    admin_token = await get_admin_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp.raise_for_status()
        return [{"id": r["id"], "name": r["name"]} for r in resp.json()]


async def add_realm_role_to_user(user_id: str, role_name: str) -> bool:
    """Assign a realm role to a Keycloak user."""
    roles = await get_realm_roles()
    role = next((r for r in roles if r["name"] == role_name), None)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    admin_token = await get_admin_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users/{user_id}/role-mappings/realm",
            json=[{"id": role["id"], "name": role["name"]}],
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204


async def remove_realm_role_from_user(user_id: str, role_name: str) -> bool:
    """Remove a realm role from a Keycloak user."""
    roles = await get_realm_roles()
    role = next((r for r in roles if r["name"] == role_name), None)
    if not role:
        return False

    admin_token = await get_admin_token()
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users/{user_id}/role-mappings/realm",
            json=[{"id": role["id"], "name": role["name"]}],
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204


async def update_keycloak_user(user_id: str, data: dict) -> bool:
    """Update a Keycloak user's profile."""
    admin_token = await get_admin_token()
    payload = {}
    if "email" in data:
        payload["email"] = data["email"]
    if "firstName" in data:
        payload["firstName"] = data["firstName"]
    if "lastName" in data:
        payload["lastName"] = data["lastName"]
    if "attributes" in data:
        payload["attributes"] = data["attributes"]

    if not payload:
        return True

    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.REALM}/users/{user_id}",
            json=payload,
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204
