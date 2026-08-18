"""
Keycloak service - handles communication with Keycloak identity provider.

This module provides functions to interact with Keycloak's API:
- Refresh tokens (get new access tokens using a refresh token)
- Revoke tokens (logout from Keycloak side)
- Get JWKS (JSON Web Key Set for verifying JWT signatures)

Keycloak is the identity provider that handles user authentication.
This backend acts as a client to Keycloak, using the client credentials
flow (client_id + client_secret) plus user credentials when needed.

Every function accepts an optional `realm` (logical realm name). `None`
(and any caller that omits it) resolves to the default realm — identical
URLs and behavior to the pre-multi-realm code.
"""
import json
import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.features.auth.realm_registry import get_realm_config

logger = logging.getLogger(__name__)


def _cfg_for(realm: str | None):
    return get_realm_config(realm)


async def refresh_keycloak_token(refresh_token: str, realm: str | None = None) -> dict:
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
    cfg = _cfg_for(realm)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            cfg.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to refresh Keycloak token")

    return response.json()


async def revoke_keycloak_token(refresh_token: str, realm: str | None = None) -> bool:
    """
    Revoke a refresh token in Keycloak (logout).
    
    This tells Keycloak that the refresh token is no longer valid.
    After this, the user can't refresh their session anymore.
    
    Returns:
        bool: True if revocation was successful, False otherwise
    """
    cfg = _cfg_for(realm)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            cfg.logout_url,
            data={
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
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


async def get_jwks(realm: str | None = None) -> dict:
    cfg = _cfg_for(realm)
    async with httpx.AsyncClient() as client:
        response = await client.get(cfg.jwks_url)
        response.raise_for_status()
        return response.json()


async def get_admin_token(realm: str | None = None) -> str:
    """Get admin access token using client credentials."""
    cfg = _cfg_for(realm)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            cfg.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to get admin token")
    return response.json()["access_token"]


async def create_keycloak_user(
    username: str,
    email: str,
    realm: str | None = None,
    first_name: str = "NA",
    last_name: str = "NA",
) -> str:
    """Create a user in Keycloak. Returns user ID."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{cfg.admin_base}/users",
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
                "firstName": first_name,
                "lastName": last_name,
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


async def set_keycloak_password(user_id: str, password: str, realm: str | None = None) -> bool:
    """Set password for a user in Keycloak."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{cfg.admin_base}/users/{user_id}/reset-password",
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


async def get_realm_roles(realm: str | None = None) -> list:
    """Fetch all realm roles from Keycloak."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{cfg.admin_base}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp.raise_for_status()
        return [{"id": r["id"], "name": r["name"]} for r in resp.json()]


async def get_user_realm_roles(user_id: str, realm: str | None = None) -> list:
    """Get realm role names assigned to a specific user."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{cfg.admin_base}/users/{user_id}/role-mappings/realm",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if resp.status_code != 200:
            return []
        return [r["name"] for r in resp.json()]


async def add_realm_role_to_user(user_id: str, role_name: str, realm: str | None = None) -> bool:
    """Assign a realm role to a Keycloak user."""
    roles = await get_realm_roles(realm)
    role = next((r for r in roles if r["name"] == role_name), None)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{cfg.admin_base}/users/{user_id}/role-mappings/realm",
            json=[{"id": role["id"], "name": role["name"]}],
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204


async def remove_realm_role_from_user(user_id: str, role_name: str, realm: str | None = None) -> bool:
    """Remove a realm role from a Keycloak user."""
    roles = await get_realm_roles(realm)
    role = next((r for r in roles if r["name"] == role_name), None)
    if not role:
        return False

    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{cfg.admin_base}/users/{user_id}/role-mappings/realm",
            json=[{"id": role["id"], "name": role["name"]}],
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204


async def get_keycloak_user_by_email(email: str, realm: str | None = None) -> dict:
    """
    Look up a Keycloak user by email address via Admin API.
    
    Returns the first matching user dict with keys: id, username, email.
    Returns empty dict if no user found.
    """
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{cfg.admin_base}/users",
            params={"email": email, "max": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    if resp.status_code != 200:
        return {}
    users = resp.json()
    if not users:
        return {}
    u = users[0]
    return {"id": u["id"], "username": u.get("username", ""), "email": u.get("email", "")}


async def delete_keycloak_user(user_id: str, realm: str | None = None) -> bool:
    """Permanently delete a Keycloak user via the Admin API."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{cfg.admin_base}/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    return resp.status_code == 204


async def update_keycloak_user(user_id: str, data: dict, realm: str | None = None) -> bool:
    """Update a Keycloak user's profile."""
    cfg = _cfg_for(realm)
    admin_token = await get_admin_token(realm)
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
            f"{cfg.admin_base}/users/{user_id}",
            json=payload,
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
    return resp.status_code == 204