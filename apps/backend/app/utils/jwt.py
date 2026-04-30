"""
JWT (JSON Web Token) utility functions.

This module provides helper functions for working with JWT tokens
issued by Keycloak. It focuses on extracting user information from
tokens without verifying signatures (since we trust tokens from Keycloak).

Note: For proper JWT verification (checking signature, expiry, etc.),
use the JWKS from Keycloak with a library like python-jose.
"""
import logging

from jose import jwt

logger = logging.getLogger(__name__)


def get_user_info_from_token(access_token: str) -> dict:
    """
    Extract user information from a Keycloak access token.
    
    This reads the token WITHOUT verifying the signature.
    We do this because:
    1. We got the token directly from Keycloak (via our backend)
    2. We trust our own connection to Keycloak
    3. We just need to read the user info (sub, username, email, roles)
    
    Args:
        access_token: The JWT access token from Keycloak
        
    Returns:
        dict: User info with keys: sub, username, email, roles
              Returns empty dict if extraction fails
    """
    try:
        # Decode without verification to get the payload (user info)
        payload = jwt.get_unverified_claims(access_token)
        return {
            "sub": payload.get("sub"),  # Keycloak's unique user ID
            "username": payload.get("preferred_username"),  # The username
            "email": payload.get("email"),  # User's email
            "roles": payload.get("realm_access", {}).get("roles", []),  # User's roles
        }
    except Exception as e:
        logger.error(f"Error extracting user info: {e}")
        return {}
