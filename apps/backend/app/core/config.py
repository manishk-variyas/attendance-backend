"""
Application configuration settings.

This module uses pydantic-settings to load configuration from:
1. Environment variables (highest priority)
2. .env file (if present)
3. Default values defined below

All settings can be overridden by setting environment variables.
For example, to change the Keycloak URL, set KEYCLOAK_URL=http://...

How it works:
- Settings class inherits from BaseSettings which automatically reads env vars
- Type hints are used to convert env var strings to proper types
- Computed properties (JWKS_URL, TOKEN_URL, etc.) build URLs from base settings
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Keycloak identity provider settings
    # These are used to connect to Keycloak for authentication
    KEYCLOAK_URL: str = "http://keycloak:8080"  # Keycloak server URL (internal Docker network)
    REALM: str = "attendance-app"  # Keycloak realm name
    KEYCLOAK_CLIENT_ID: str = "backend-client"  # Client ID for this backend app
    KEYCLOAK_CLIENT_SECRET: str = "best-practice-secret-12345"  # Client secret (CHANGE IN PRODUCTION!)

    # Session settings
    # Sessions are stored in Redis with these settings
    SECRET_KEY: str = "change-me-in-production-use-strong-random-key"  # Used for signing (CHANGE IN PRODUCTION!)
    ALGORITHM: str = "HS256"  # JWT signing algorithm
    SESSION_EXPIRE_HOURS: int = 24  # How long sessions last in hours

    # URLs for different services
    BACKEND_URL: str = "http://localhost:8000"  # This backend's public URL
    FRONTEND_URL: str = "http://localhost:5173"  # Frontend app URL (for CORS)

    # Cookie security settings
    COOKIE_SECURE: bool = False  # Set to True in production (requires HTTPS)

    # Database connections
    DATABASE_URL: str = "postgresql://keycloak:keycloak@keycloak-db:5432/keycloak"  # PostgreSQL for Keycloak
    REDIS_URL: str = "redis://redis:6379"  # Redis for session storage

    # CORS - list of allowed frontend origins
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Rate limiting - max requests per second per IP
    RATE_LIMIT_PER_SECOND: int = 100

    @property
    def JWKS_URL(self) -> str:
        """URL to fetch Keycloak's JSON Web Key Set (public keys for verifying JWTs)."""
        return f"{self.KEYCLOAK_URL}/realms/{self.REALM}/protocol/openid-connect/certs"

    @property
    def ISSUER_URL(self) -> str:
        """URL of the Keycloak realm (used as the 'issuer' in JWT tokens)."""
        return f"{self.KEYCLOAK_URL}/realms/{self.REALM}"

    @property
    def TOKEN_URL(self) -> str:
        """URL to exchange credentials for tokens (login endpoint)."""
        return f"{self.KEYCLOAK_URL}/realms/{self.REALM}/protocol/openid-connect/token"

    @property
    def LOGOUT_URL(self) -> str:
        """URL to revoke/refresh tokens in Keycloak."""
        return f"{self.KEYCLOAK_URL}/realms/{self.REALM}/protocol/openid-connect/logout"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
